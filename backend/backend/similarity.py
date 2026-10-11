"""Embedding model loader for semantic similarity.

Loads the local fine-tuned HACKNETIX Sentence Transformer from MODEL_ROOT. It never downloads anything:
HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE are forced on before the library is imported.

MODEL_ROOT can contain either:
  * the unzipped model folder (a folder with modules.json, or config.json + tokenizer), or
  * a .zip of it. The zip is extracted once, safely, next to itself.
"""
import logging
import os
import re
import threading
import zipfile
import zlib
from pathlib import Path

import numpy as np

log = logging.getLogger("hacknetix.similarity")
_lock = threading.Lock()
_embedder = None


class ModelNotAvailable(RuntimeError):
    pass


# ---------------------------------------------------------------- locating the model
def _looks_like_model(d: Path) -> bool:
    if (d / "modules.json").exists():
        return True
    return (d / "config.json").exists() and ((d / "tokenizer.json").exists() or (d / "vocab.txt").exists())


def _find_model_dir(root: Path):
    if not root.exists():
        return None
    candidates = [root] + sorted(p for p in root.rglob("*") if p.is_dir())
    # prefer a real sentence-transformers folder
    for d in candidates:
        if (d / "modules.json").exists():
            return d
    for d in candidates:
        if _looks_like_model(d):
            return d
    return None


def _safe_extract(zpath: Path, dest: Path):
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zpath) as zf:
        for m in zf.infolist():
            target = (dest / m.filename).resolve()
            if target != dest and dest not in target.parents:
                raise ModelNotAvailable(f"Unsafe path inside zip: {m.filename}")
        zf.extractall(dest)


def resolve_model_dir(model_root, zip_path=""):
    root = Path(model_root)
    root.mkdir(parents=True, exist_ok=True)
    found = _find_model_dir(root)
    if found:
        return found
    zips = [Path(zip_path)] if zip_path else sorted(root.glob("*.zip"))
    for z in zips:
        if z.is_file():
            dest = root / z.stem
            log.info("Extracting model zip %s -> %s", z, dest)
            _safe_extract(z, dest)
            found = _find_model_dir(dest)
            if found:
                return found
    return None


# ---------------------------------------------------------------- embedders
class SentenceEmbedder:
    name = "hacknetix_sentence_transformer"
    default_threshold = 0.60

    def __init__(self, model_dir: Path):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        from sentence_transformers import SentenceTransformer  # imported late on purpose
        self.model = SentenceTransformer(str(model_dir), device="cpu")
        self.dim = self.model.get_sentence_embedding_dimension()
        self._enc_lock = threading.Lock()

    def encode(self, texts):
        with self._enc_lock:
            vecs = self.model.encode(list(texts), normalize_embeddings=True,
                                     convert_to_numpy=True, show_progress_bar=False)
        return np.asarray(vecs, dtype=np.float32)

    def threshold(self, override=None):
        return self.default_threshold if override is None else override


_STOP = set("the a an and or of to in on at is are was were be been it this that for with from by as but not "
            "there has have had very my our we i you they them their its into out up so than then".split())


class HashingEmbedder:
    """Development-only bag-of-words hashing fallback."""
    name = "fallback-hashing (not HACKNETIX model)"
    default_threshold = 0.35
    dim = 512

    def encode(self, texts):
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            words = [w for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in _STOP]
            feats = words + [a + "_" + b for a, b in zip(words, words[1:])]
            for f in feats:
                out[i, zlib.crc32(f.encode()) % self.dim] += 1.0
            n = np.linalg.norm(out[i])
            if n:
                out[i] /= n
        return out

    def threshold(self, override=None):
        return self.default_threshold if override is None else override


# ---------------------------------------------------------------- singleton access
def init_embedder(model_root, zip_path="", allow_fallback=False):
    global _embedder
    with _lock:
        if _embedder is not None:
            return _embedder
        model_dir = resolve_model_dir(model_root, zip_path)
        error = None
        if model_dir:
            try:
                _embedder = SentenceEmbedder(model_dir)
                log.info("Loaded %s from %s (dim=%s)", _embedder.name, model_dir, _embedder.dim)
                return _embedder
            except Exception as exc:  # missing sentence-transformers, corrupt weights, ...
                error = exc
                log.exception("Could not load model from %s", model_dir)
        if allow_fallback:
            log.warning("USING FALLBACK EMBEDDER. Results are NOT from the HACKNETIX model. Development only.")
            _embedder = HashingEmbedder()
            return _embedder
        raise ModelNotAvailable(
            "The fine-tuned HACKNETIX Sentence Transformer could not be loaded. "
            f"Looked in: {Path(model_root).resolve()}. "
            "Put your model zip (or the unzipped folder) there and run `pip install sentence-transformers`. "
            + (f"Underlying error: {error!r}. " if error else "")
            + "For UI-only development you can set ALLOW_FALLBACK_EMBEDDER=1."
        )


def get_embedder():
    if _embedder is None:
        raise ModelNotAvailable("Embedder has not been initialised.")
    return _embedder


def reset_embedder():          # used by tests
    global _embedder
    _embedder = None
