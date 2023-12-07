class SentencePieceTokenizer:
    def __init__(self, model_identifier):
        from sentencepiece import SentencePieceProcessor
        self.model_identifier = model_identifier
        self.tokenizer = SentencePieceProcessor(model_identifier)
        
    def get_token_by_ids(self, ids):
        return self.tokenizer.id_to_piece(ids)

    @staticmethod
    def is_available(model_identifier):
        # Returns True if this tokenizer implementation is available for the given model identifier.
        try:
            from sentencepiece import SentencePieceProcessor
            return True
        except ImportError:
            return False
    
    def __getstate__(self):
        return {
            "model_identifier": self.model_identifier,
            "tokenizer": self.tokenizer
        }
    
    def __setstate__(self, state):
        from sentencepiece import SentencePieceProcessor
        self.model_identifier = state["model_identifier"]
        self.tokenizer = state["tokenizer"]
    
    def tokenize(self, text, asbytes=False, add_special_tokens=False):
        if text in [" ", "\n"]:
            return ["▁"] if text==" " else ["<0x0A>"]
        if asbytes:
            ids = self(text)["input_ids"]
            return self.decode_tokens_bytes(ids)
        return self.tokenizer.tokenize(text)
    
    def decode_tokens_bytes(self, ids):
        """
        Converts a list of token ids into a list of token bytes.
        """
        # translate 'ids' into a bytes representation
        return [f"{i}".encode("utf-8") for i in ids]

    def __call__(self, text, add_special_tokens=False):
        prepend_dummy_tokens = [""] if text.startswith("[INST]") else ["@", "^", ""]
        for dummy_token in prepend_dummy_tokens:
            text_to_tokenize = dummy_token + text

            result = {"input_ids": self.tokenizer.Encode(text_to_tokenize, add_bos=True)}
            if len(result["input_ids"]) <= 2 and dummy_token != "":
                # "Tokenized text '{}' was merged with dummy token @ into '{}'".format(text_to_tokenize, [self.tokenizer.convert_ids_to_tokens(i) for i in result["input_ids"]])
                continue
            offset = 2 if len(dummy_token) > 0 else 1
            result["input_ids"] = result["input_ids"][offset:]

            return result

        assert (
            False
        ), f"LLamaTransformersTokenizer.__call__ failed to workaround tokenization issue for '{text}'"
    
    def convert_bytes_to_string(self, token_bytes):
        """
        Converts a list of token bytes into a string.

        It must hold that self.convert_bytes_to_string(self.decode_tokens_bytes(ids)) == self.decode(ids).
        """
        if len(token_bytes)==1:
            tokens = self.get_token_by_ids([int(token.decode("utf-8")) for token in token_bytes])
            if tokens[0].startswith("▁"):
                ids = self.convert_token_bytes_to_ids(token_bytes)
                res = self.tokenizer.decode(ids)
                return f" {res}"
        
        token_bytes = [str(self.tokenizer.bos_id()).encode("utf-8")] + token_bytes
        ids = self.convert_token_bytes_to_ids(token_bytes)
        
        # left-pad with dummy tokens, to avoid automatic removal of leading spaces
        lpad_str = "<|lpad|>"
        lpad = self.tokenizer.Encode(lpad_str, add_bos=False)

        return self.tokenizer.Decode(lpad + ids)[len(lpad_str):]
    
    def decode(self, ids, clean_up_tokenization_spaces=True):
        """
        Converts a list of token ids into a string.
        """
        return self.tokenizer.decode(ids)
    
    def convert_token_bytes_to_ids(self, tokens):
        """
        Converts a list of token bytes into a list of token ids.

        Inverse of self.decode_tokens_bytes.
        """
        return [int(t.decode("utf-8")) for t in tokens]
    
    def convert_tokens_to_string(self, tokens):
        return self.tokenizer.Decode(tokens)

    @property
    def vocab(self):
        return { self.tokenizer.id_to_piece(id): id for id in range(self.tokenizer.get_piece_size()) }

    @property
    def vocab_size(self):
        return self.tokenizer.vocab_size()

    @property
    def eos_token_id(self):
        return self.tokenizer.eos_id()
    
    @property
    def bos_token_id(self):
        return self.tokenizer.bos_id()
    
    @property
    def name(self):
        return f"sentencepiece-{self.model_identifier}"
    
    def backend(self):
        return f"sentencepiece {type(self.tokenizer).__name__}"