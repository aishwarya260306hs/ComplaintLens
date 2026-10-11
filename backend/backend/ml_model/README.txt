The app uses the supplied fine-tuned model at ../ai_model/models/hacknetix_sentence_transformer
by default. This directory is not used unless MODEL_ROOT is overridden.

MODEL_ROOT may point to another local Sentence Transformers model folder or a folder
containing a model zip. The app never downloads a model. If the configured model is
missing or cannot be loaded, startup fails unless the development-only fallback is enabled.
