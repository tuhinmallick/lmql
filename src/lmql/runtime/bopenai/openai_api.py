import os

if "LMQL_BROWSER" in os.environ:
    # use mocked aiohttp for browser (redirects to JS for network requests)
    import lmql.runtime.maiohttp as aiohttp
    LMQL_BROWSER = True
else:
    # use real aiohttp for python
    import aiohttp
    LMQL_BROWSER = False

import json
import time
import asyncio

from lmql.runtime.stats import Stats
from lmql.api.blobs import Blob
from lmql.runtime.tracing import Tracer
from lmql.models.model_info import model_info

class OpenAIAPILimitationError(Exception): pass
class OpenAIStreamError(Exception): pass
class OpenAIRateLimitError(OpenAIStreamError): pass

class Capacity: pass
Capacity.total = 32000 # defines the total capacity available to allocate to different token streams that run in parallel
# a value of 80000 averages around 130k tok/min on davinci with beam_var (lower values will decrease the rate and avoid rate limiting)
Capacity.reserved = 0

stream_semaphore = None

api_stats = Stats("openai-api")

# models that do not support 'logprobs' and 'echo' by OpenAI limitations
MODELS_WITHOUT_ECHO_LOGPROBS = [
    "gpt-3.5-turbo-instruct"
]

class CapacitySemaphore:
    def __init__(self, capacity):
        self.capacity = capacity

    async def __aenter__(self):
        # wait for self.capacity > capacity
        while True:
            if Capacity.reserved >= Capacity.total:
                await asyncio.sleep(0.5)
            else:
                Capacity.reserved += self.capacity
                break

    async def __aexit__(self, *args):
        Capacity.reserved -= self.capacity

class concurrent:
    def __init__(self, task):
        self.task = task

    def __enter__(self):
        self.task = asyncio.create_task(self.task)
        return self
    
    def __exit__(self, *args):
        self.task.cancel()

def is_azure_chat(kwargs):
    if "api_config" not in kwargs: return False
    api_config = kwargs["api_config"]
    if "api_type" not in api_config: 
        return os.environ.get("OPENAI_API_TYPE", "azure") == "azure-chat"
    return ("api_type" in api_config and "azure-chat" in api_config.get("api_type", ""))

def is_chat_model(kwargs):
    model = kwargs.get("model", None)
    api_config = kwargs.get("api_config", {})

    return model_info(model).is_chat_model or \
           is_azure_chat(kwargs) or \
           kwargs.get("api_config", {}).get("chat_model", False)

async def complete(**kwargs):
    if is_chat_model(kwargs):
        async for r in chat_api(**kwargs): yield r
    else:
        async for r in completion_api(**kwargs): yield r

global tokenizers
tokenizers = {}

def tokenize(text, tokenizer, openai_byte_encoding=False):
    ids = tokenizer(text)["input_ids"]
    raw = tokenizer.decode_bytes(ids)
    if not openai_byte_encoding:
        return raw
    raw = [str(t)[2:-1] for t in raw]
    return [
        t.encode("utf-8").decode("unicode_escape")
        if "\\x" not in t
        else f"bytes:{t}"
        for t in raw
    ]

def tagged_segments(s):
    import re
    segments = []
    current_tag = None
    offset = 0
    for m in re.finditer(r"<lmql:(.*?)\/>", s):
        if m.start() - offset > 0:
            segments.append({"tag": current_tag, "text": s[offset:m.start()]})
        current_tag = m.group(1)
        offset = m.end()
    segments.append({"tag": current_tag, "text": s[offset:]})
    return segments


def get_azure_config(model, api_config):
    endpoint = api_config.get("endpoint", None)
    api_type = api_config.get("api_type", os.environ.get("OPENAI_API_TYPE", ""))

    if api_type in ["azure", "azure-chat"]:
        api_base = api_config.get("api_base", None) or os.environ.get("OPENAI_API_BASE", None)
        assert api_base is not None, "Please specify the Azure API base URL as 'api_base' or environment variable OPENAI_API_BASE"
        api_version = api_config.get("api_version", None) or os.environ.get("OPENAI_API_VERSION", "2023-05-15")
        deployment = api_config.get("api_deployment", None) or os.environ.get("OPENAI_DEPLOYMENT", model)

        deployment_specific_api_key = f"OPENAI_API_KEY_{deployment.upper()}"
        api_key = api_config.get("api_key", None) or os.environ.get(deployment_specific_api_key, None) or os.environ.get("OPENAI_API_KEY", None)
        assert (
            api_key is not None
        ), f"Please specify the Azure API key as 'api_key' or environment variable OPENAI_API_KEY or {deployment_specific_api_key}"

        is_chat = api_type == "azure-chat"

        endpoint = (
            f"{api_base}/openai/deployments/{deployment}/chat/completions"
            if is_chat
            else f"{api_base}/openai/deployments/{deployment}/completions"
        )
        if api_version is not None:
            endpoint += f"?api-version={api_version}"

        headers = {
            "Content-Type": "application/json",
            "api-key": api_key,
        }

        if api_config.get("verbose", False) or os.environ.get("OPENAI_VERBOSE", "0") == "1":
            print(f"Using Azure API endpoint: {endpoint}", is_chat, flush=True)

        return endpoint, headers

    return None

def get_endpoint_and_headers(kwargs):
    model = kwargs["model"]
    api_config = kwargs.pop("api_config", {})
    endpoint = api_config.get("endpoint", None)

    # try to get azure config from endpoint or env
    azure_config = get_azure_config(model, api_config)
    if azure_config is not None:
        return azure_config
    
    # otherwise use custom endpoint as plain URL without authorization
    if endpoint is not None:
        if not endpoint.startswith("http"):
            endpoint = f"http://{endpoint}"
        return endpoint, {
            "Content-Type": "application/json"
        }
    
    # use standard public API config
    from lmql.runtime.openai_secret import openai_secret, openai_org
    headers = {
        "Authorization": f"Bearer {openai_secret}",
        "Content-Type": "application/json",
    }
    if openai_org:
        headers['OpenAI-Organization'] = openai_org
    if is_chat_model({**kwargs, "api_config": api_config}):
        endpoint = "https://api.openai.com/v1/chat/completions"
    else:
        endpoint = "https://api.openai.com/v1/completions"
    return endpoint, headers

async def chat_api(**kwargs):
    global stream_semaphore

    num_prompts = len(kwargs["prompt"])
    api_config = kwargs.get("api_config", {})
    tokenizer = api_config.get("tokenizer")
    assert tokenizer is not None, "internal error: chat_api expects an 'api_config' with a 'tokenizer: LMQLTokenizer' mapping in your API payload"

    max_tokens = kwargs.get("max_tokens", 0)
    if max_tokens == -1:
        kwargs.pop("max_tokens")

    assert (
        "logit_bias" not in kwargs.keys()
    ), "Chat API models do not support advanced constraining of the output, please use no or less complicated constraints."
    prompt_tokens = tokenize(kwargs["prompt"][0], tokenizer=tokenizer, openai_byte_encoding=True)

    timeout = kwargs.pop("timeout", 1.5)
    echo = kwargs.pop("echo")
    tracer: Tracer = kwargs.pop("tracer", None)

    if echo:
        yield {
            "choices": [
                {
                    "text": kwargs["prompt"][0],
                    "index": 0,
                    "finish_reason": None,
                    "logprobs": {
                        "text_offset": [0 for t in prompt_tokens],
                        "token_logprobs": [0.0 for t in prompt_tokens],
                        "tokens": prompt_tokens,
                        "top_logprobs": [{t: 0.0} for t in prompt_tokens],
                    },
                }
            ]
        }
    if max_tokens == 0:
        return

    assert (
        len(kwargs["prompt"]) == 1
    ), "chat API models do not support batched processing"

    messages = []
    for s in tagged_segments(kwargs["prompt"][0]):
        role = "user"
        tag = s["tag"]
        if tag == "system":
            role = "system"
        elif tag == "assistant":
            role = "assistant"
        elif tag == "user":
            role = "user"
        elif tag is None:
            role = "user"
        else:
            print(f"warning: {tag} is not a valid tag for the OpenAI chat API. Please use one of :system, :user or :assistant.")

        messages.append({
            "role": role, 
            "content": s["text"]
        })

    del kwargs["prompt"]
    kwargs["messages"] = messages

    needs_space = True # messages[-1]["content"][-1] != " "

    del kwargs["logprobs"]

    async with CapacitySemaphore(num_prompts * max_tokens):
        
        current_chunk = ""
        stream_start = time.time()

        async with aiohttp.ClientSession() as session:
            endpoint, headers = get_endpoint_and_headers(kwargs)

            handle = tracer.event("openai.ChatCompletion", {
                "endpoint": endpoint,
                "headers": headers,
                "tokenizer": str(tokenizer),
                "kwargs": kwargs
            })

            if api_config.get("verbose", False) or os.environ.get("LMQL_VERBOSE", "0") == "1" or api_config.get("chatty_openai", False):
                print(f"openai complete: {kwargs}", flush=True)

            async with session.post(
                                endpoint,
                                headers=headers,
                                json={**kwargs},
                        ) as resp:
                last_chunk_time = time.time()
                sum_chunk_times = 0
                n_chunks = 0
                current_chunk_time = 0

                async def chunk_timer():
                    nonlocal last_chunk_time, sum_chunk_times, n_chunks, current_chunk_time
                    while True:
                        await asyncio.sleep(0.5)
                        current_chunk_time = time.time() - last_chunk_time
                        # print("Average chunk time:", sum_chunk_times / n_chunks, "Current chunk time:", current_chunk_time)
                        # print("available capacity", Capacity.total - Capacity.reserved, "reserved capacity", Capacity.reserved, "total capacity", Capacity.total, flush=True)

                        if current_chunk_time > timeout:
                            print("Token stream took too long to produce next chunk, re-issuing completion request. Average chunk time:", sum_chunk_times / max(1,n_chunks), "Current chunk time:", current_chunk_time, flush=True)
                            resp.close()
                            raise OpenAIStreamError("Token stream took too long to produce next chunk.")

                received_text = ""

                with concurrent(chunk_timer()):
                    async for chunk in resp.content.iter_any():
                        chunk = chunk.decode("utf-8")
                        current_chunk += chunk
                        is_done = current_chunk.strip().endswith("[DONE]")

                        while "data: " in current_chunk:
                            chunks = current_chunk.split("\ndata: ")
                            while len(chunks[0]) == 0:
                                chunks = chunks[1:]
                            if len(chunks) == 1:
                                # last chunk may be incomplete
                                break
                            complete_chunk = chunks[0].strip()
                            current_chunk = "\ndata: ".join(chunks[1:])

                            if complete_chunk.startswith("data: "):
                                complete_chunk = complete_chunk[len("data: "):]

                            if len(complete_chunk.strip()) == 0: 
                                continue
                            if complete_chunk == "[DONE]": 
                                return

                            n_chunks += 1
                            sum_chunk_times += time.time() - last_chunk_time
                            last_chunk_time = time.time()

                            try:
                                data = json.loads(complete_chunk)
                            except json.decoder.JSONDecodeError:
                                print("Failed to decode JSON:", [complete_chunk])

                            if "error" in data.keys():
                                message = data["error"]["message"]
                                if "rate limit" in message.lower():
                                    raise OpenAIRateLimitError(
                                        f"{message}local client capacity{str(Capacity.reserved)}"
                                    )
                                else:
                                    raise OpenAIStreamError(
                                        f"{message} (after receiving {n_chunks} chunks. Current chunk time: {str(time.time() - last_chunk_time)} Average chunk time: {str(sum_chunk_times / max(1, n_chunks))})",
                                        "Stream duration:",
                                        time.time() - stream_start,
                                    )

                            choices = []
                            for i, c in enumerate(data["choices"]):
                                delta = c["delta"]
                                # skip non-content annotations for now
                                if "content" not in delta:
                                    if len(delta) == 0: # {} indicates end of stream
                                        choices.append({
                                            "text": "",
                                            "index": c["index"],
                                            "finish_reason": c["finish_reason"],
                                            "logprobs": {
                                                "text_offset": [],
                                                "token_logprobs": [],
                                                "tokens": [],
                                                "top_logprobs": []
                                            }
                                        })
                                    continue

                                handle.add(f"result[{i}]", [delta])

                                text = delta["content"]
                                if len(text) == 0:
                                    continue

                                tokens = tokenize((" " if received_text == "" and needs_space else "") + text, tokenizer=tokenizer, openai_byte_encoding=True)
                                received_text += text

                                # convert tokens to OpenAI format
                                tokens = [str(t) for t in tokens]

                                choices.append({
                                    "text": text,
                                    "index": c["index"],
                                    "finish_reason": c["finish_reason"],
                                    "logprobs": {
                                        "text_offset": [0 for _ in range(len(tokens))],
                                        "token_logprobs": [0.0 for _ in range(len(tokens))],
                                        "tokens": tokens,
                                        "top_logprobs": [{t: 0.0} for t in tokens]
                                    }
                                })
                            data["choices"] = choices

                            yield data

                        if is_done: break

                resp.close()

                if current_chunk.strip() == "[DONE]":
                    return
                try:
                    last_message = json.loads(current_chunk.strip())
                    message = last_message.get("error", {}).get("message", "")
                    if "rate limit" in message.lower():
                        raise OpenAIRateLimitError(
                            f"{message}local client capacity{str(Capacity.reserved)}"
                        )
                    else:   
                        raise OpenAIStreamError(
                            f"{message} (after receiving {str(n_chunks)} chunks. Current chunk time: {str(time.time() - last_chunk_time)} Average chunk time: {str(sum_chunk_times / max(1, n_chunks))})",
                            "Stream duration:",
                            time.time() - stream_start,
                        )
                except json.decoder.JSONDecodeError:
                    raise OpenAIStreamError("Error in API response:", current_chunk)
    
async def completion_api(**kwargs):
    global stream_semaphore

    num_prompts = len(kwargs["prompt"])
    timeout = kwargs.pop("timeout", 1.5)
    tracer = kwargs.pop("tracer", None)

    max_tokens = kwargs.get("max_tokens")
    # if no token limit is set, use 1024 as a generous chunk size
    # (completion models require max_tokens to be set)
    if max_tokens == -1: 
        # not specifying anything will use default chunk size 16
        # specifying a higher value may error on some models
        kwargs["max_tokens"] = 1024

    assert not (LMQL_BROWSER and "logit_bias" in kwargs and "gpt-3.5-turbo" in kwargs["model"]), "gpt-3.5-turbo completion models do not support logit_bias in the LMQL browser distribution, because the required tokenizer is not available in the browser. Please use a local installation of LMQL to use logit_bias with gpt-3.5-turbo models."

    model = kwargs["model"]
    echo = kwargs.get("echo", False)
    api_config = kwargs.get("api_config", {})
    tokenizer = api_config.get("tokenizer")

    if model in MODELS_WITHOUT_ECHO_LOGPROBS and echo:
        if max_tokens == 0:
            raise OpenAIAPILimitationError("The underlying requests to the OpenAI API with model '{}' are blocked by OpenAI's API limitations. Please use a different model to leverage this form of querying (e.g. distribution clauses or scoring).".format(model))

        kwargs["echo"] = False
        batch_prompt_tokens = [tokenize(prompt, tokenizer=tokenizer, openai_byte_encoding=True) for prompt in kwargs["prompt"]]

        if echo:
            yield {
                "choices": [
                    {
                        "text": kwargs["prompt"][i],
                        "index": i,
                        "finish_reason": None,
                        "logprobs": {
                            "text_offset": [0 for t in prompt_tokens],
                            "token_logprobs": [0.0 for t in prompt_tokens],
                            "tokens": prompt_tokens,
                            "top_logprobs": [{t: 0.0} for t in prompt_tokens],
                        },
                    }
                    for i, prompt_tokens in enumerate(batch_prompt_tokens)
                ]
            }
    async with CapacitySemaphore(num_prompts):
        
        current_chunk = ""
        stream_start = time.time()


        async with aiohttp.ClientSession() as session:
            api_config = kwargs.get("api_config", {})
            tokenizer = api_config.get("tokenizer")

            endpoint, headers = get_endpoint_and_headers(kwargs)

            handle = tracer.event("openai.Completion", {
                "endpoint": endpoint,
                "headers": headers,
                "tokenizer": str(tokenizer),
                "kwargs": kwargs
            })

            if api_config.get("verbose", False) or os.environ.get("LMQL_VERBOSE", "0") == "1" or api_config.get("chatty_openai", False):
                print(f"openai complete: {kwargs}", flush=True)

            async with session.post(
                                endpoint,
                                headers=headers,
                                json={**kwargs},
                        ) as resp:
                last_chunk_time = time.time()
                sum_chunk_times = 0
                n_chunks = 0
                current_chunk_time = 0

                async def chunk_timer():
                    nonlocal last_chunk_time, sum_chunk_times, n_chunks, current_chunk_time
                    while True:
                        await asyncio.sleep(0.5)
                        current_chunk_time = time.time() - last_chunk_time
                        # print("Average chunk time:", sum_chunk_times / n_chunks, "Current chunk time:", current_chunk_time)
                        # print("available capacity", Capacity.total - Capacity.reserved, "reserved capacity", Capacity.reserved, "total capacity", Capacity.total, flush=True)

                        if current_chunk_time > timeout:
                            print("Token stream took too long to produce next chunk, re-issuing completion request. Average chunk time:", sum_chunk_times / max(1,n_chunks), "Current chunk time:", current_chunk_time, flush=True)
                            resp.close()
                            raise OpenAIStreamError("Token stream took too long to produce next chunk.")

                with concurrent(chunk_timer()):
                    async for chunk in resp.content.iter_any():
                        chunk = chunk.decode("utf-8")
                        current_chunk += chunk
                        is_done = current_chunk.strip().endswith("[DONE]")

                        while "data: " in current_chunk:
                            chunks = current_chunk.split("\ndata: ")
                            while len(chunks[0]) == 0:
                                chunks = chunks[1:]
                            if len(chunks) == 1:
                                # last chunk may be incomplete
                                break
                            complete_chunk = chunks[0].strip()
                            current_chunk = "\ndata: ".join(chunks[1:])

                            if complete_chunk.startswith("data: "):
                                complete_chunk = complete_chunk[len("data: "):]

                            if len(complete_chunk.strip()) == 0: 
                                continue
                            if complete_chunk == "[DONE]":
                                return

                            if n_chunks == 0:
                                api_stats.times["first-chunk-latency"] = api_stats.times.get("first-chunk-latency", 0) + (time.time() - stream_start)

                            n_chunks += 1
                            sum_chunk_times += time.time() - last_chunk_time
                            last_chunk_time = time.time()

                            try:
                                data = json.loads(complete_chunk)
                            except json.decoder.JSONDecodeError:
                                print("Failed to decode JSON:", [complete_chunk])

                            if "error" in data.keys():
                                message = data["error"]["message"]
                                if "rate limit" in message.lower():
                                    raise OpenAIRateLimitError(
                                        f"{message}local client capacity{str(Capacity.reserved)}"
                                    )
                                else:
                                    raise OpenAIStreamError(
                                        f"{message} (after receiving {n_chunks} chunks. Current chunk time: {str(time.time() - last_chunk_time)} Average chunk time: {str(sum_chunk_times / max(1, n_chunks))})",
                                        "Stream duration:",
                                        time.time() - stream_start,
                                    )

                            for i in range(len(data["choices"])):
                                handle.add(f"result[{i}]", [data["choices"][i]["text"]])

                            yield data

                        if is_done: break

                resp.close()

                if current_chunk.strip() == "[DONE]":
                    return

                try:
                    last_message = json.loads(current_chunk.strip())
                    message = last_message.get("error", {}).get("message", "")
                    if "rate limit" in message.lower():
                        raise OpenAIRateLimitError(
                            f"{message}local client capacity{str(Capacity.reserved)}"
                        )
                    else:
                        raise OpenAIStreamError((message or str(last_message)) + " (after receiving " + str(n_chunks) + " chunks. Current chunk time: " + str(time.time() - last_chunk_time) + " Average chunk time: " + str(sum_chunk_times / max(1, n_chunks)) + ")", "Stream duration:", time.time() - stream_start)
                        # raise OpenAIStreamError(last_message["error"]["message"])
                except json.decoder.JSONDecodeError:
                    raise OpenAIStreamError("Error in API response:", current_chunk)

async def main():
    import sys
    # Not sure if this should work, but prompt needs tokenizing I think
    """
    kwargs = {
        "model": "text-davinci-003",
        "prompt": "Say this is a test",
        "max_tokens": 7,
        "temperature": 0,
        "stream": True
    }

    async for chunk in complete(**kwargs):
                print(chunk)"""

    """
     Tested working with these environment variables:
        Azure config for GPT-3.5-Turbo:
            OPENAI_API_TYPE = azure
            AZURE_OPENAI_GPT-3_5-TURBO_ENDPOINT = https://{service}.openai.azure.com/openai/deployments/{gpt3.5-turbo-deployment}/chat/completions?api-version=2023-03-15-preview
            AZURE_OPENAI_GPT-3_5-TURBO_KEY = XXXXXXXXX
        Regular OpenAI credentials for GPT-3.5-Turbo:
            OPENAI_API_TYPE = openai
            OPENAI_API_KEY = XXXXXXXXX
    """

    kwargs = {
        "model": "gpt-3.5-turbo",
        "prompt": [
            tokenize("<lmql:system/> You are a helpful assistant.<lmql:user/>Hi, tell me all you know about GPT-2.")],
        "max_tokens": 512,
        "temperature": 0.,
        "stream": True,
        "echo": False,
        "logprobs": None,
    }

    async for chunk in chat_api(**kwargs):
        if len(chunk["choices"]) > 0:
            sys.stdout.write(chunk["choices"][0]["text"])


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
