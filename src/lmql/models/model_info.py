"""
This file contains a list of hard-coded model information 
for LMQL to enable ready-to-use configuration for models
that have been tested and verified to work with LMQL.
"""
from dataclasses import dataclass

@dataclass
class ModelInfo:
    is_chat_model: bool = False

def model_info(model_identifier):
    if (
        model_identifier
        in ["openai/gpt-3.5-turbo-instruct", "gpt-3.5-turbo-instruct"]
        or model_identifier not in ["openai/gpt-4", "gpt-4"]
        and "gpt-3.5-turbo" not in model_identifier
        and "openai/gpt-4" not in model_identifier
    ):
        return ModelInfo(is_chat_model=False)
    else:
        return ModelInfo(is_chat_model=True)