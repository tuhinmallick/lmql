class PythonBackedTokenizer:
    """ Custom tokenizer to be used only in a browser environment. This tokenizer only supports GPT tokenization. """
    def __init__(self, model_identifier):

        self.bos_token_id = 50256
        self.eos_token_id = 50256
        self._vocab = None
        self.model_identifier = model_identifier

    @staticmethod
    def is_available(model_identifier):
        try:
            import gpt3_tokenizer
            openai_models = [
                "ada-",
                "babbage-",
                "davinci-",
                "gpt"
            ]

            return any(m in model_identifier for m in openai_models)
        except:
            return False

    @property
    def vocab_size(self):
        return len(self.vocab)

    @property
    def vocab(self):
        if self._vocab is None:
            import gpt3_tokenizer
            self._vocab = gpt3_tokenizer._entry._encoder
        return self._vocab

    def convert_tokens_to_string(self, tokens):
        import gpt3_tokenizer
        text_with_bytes = "".join(tokens)
        textarr = [int(gpt3_tokenizer._entry._byte_decoder[x]) for x in list(text_with_bytes)]
        return bytearray(textarr).decode("utf-8")

    def tokenize(self, s, asbytes=False):
        if not asbytes:
            return self._tokenize(s)
        ids = self(s)["input_ids"]
        return self.decode_tokens_bytes(ids)

    def convert_bytes_to_string(self, token_bytes):
        ids = self.convert_token_bytes_to_ids(token_bytes)
        return self.decode(ids)

    def convert_token_bytes_to_ids(self, token_bytes):
        import gpt3_tokenizer
        result = []
        for b in token_bytes:
            if len(b) == 0:
                continue
            if b == b"<|endoftext|>":
                result.append(50256)
                continue
            tb = "".join(gpt3_tokenizer._entry._byte_encoder[str(c)] for c in b)
            tb = gpt3_tokenizer._entry._encoder[tb]
            result.append(tb)
        return result

    def decode_tokens_bytes(self, ids):
        """
        Converts a list of token ids into a list of token bytes.
        """
        import gpt3_tokenizer
        result = []
        for i in ids:
            b = gpt3_tokenizer._entry._decoder[i]
            b = [int(gpt3_tokenizer._entry._byte_decoder[c]) for c in list(b)]
            result += [bytes(b)]
        return result

    def _tokenize(self, s):
        if ("<|endoftext|>" in s):
            return [50256];
        import gpt3_tokenizer
        unpack = False
        if type(s) is not list:
            s = [s]
            unpack = True

        tokens = [[gpt3_tokenizer._entry._decoder[i] for i in gpt3_tokenizer.encode(se)] for se in s]

        return tokens[0] if unpack else tokens
        
    def decode(self, input_ids, clean_up_tokenization_spaces=None):
        import gpt3_tokenizer
        return gpt3_tokenizer.decode(input_ids)

    def __call__(self, s: str, add_special_tokens=False):
        import gpt3_tokenizer


        unpack = False
        if type(s) is not list:
            s = [s]
            unpack = True

        def encode_segment(se):
            # split segment by <|endoftext|> and encode each segment
            if "<|endoftext|>" in se:
                segments = se.split("<|endoftext|>", 1)
                return encode_segment(segments[0]) + [self.eos_token_id] + encode_segment(segments[1])

            if se == "<|endoftext|>":
                return [self.eos_token_id]
            else:
                return gpt3_tokenizer.encode(se)

        input_ids = [encode_segment(se) for se in s]

        return {"input_ids": input_ids[0]} if unpack else {"input_ids": input_ids}
        
    @property
    def name(self):
        return "python:" + "gpt3_tokenizer"
    
    def backend(self):
        import gpt3_tokenizer
        return "<module gpt3_tokenizer>"
