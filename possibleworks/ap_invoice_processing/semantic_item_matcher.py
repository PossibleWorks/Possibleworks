import hashlib
import json
import math
from collections import OrderedDict
from typing import Iterable

import openai

from possibleworks.ap_invoice_processing.doctype.ai_document_processor_settings.ai_document_processor_settings import (
	APProcessorSettings,
)


EMBEDDING_MODEL = "text-embedding-3-small"

# Process-local LRU cache (per worker). Bounded to avoid unbounded memory growth.
# text-embedding-3-small vectors are 1536 floats ≈ 12 KB each.
# 2000 entries ≈ ~24 MB maximum — safe for a long-running Frappe worker.
_EMBED_CACHE_MAX = 2000
_EMBED_CACHE: OrderedDict[str, list[float]] = OrderedDict()

# Redis cache key prefix for embeddings. Bump the version suffix to
# invalidate all cached vectors (e.g., after changing EMBEDDING_MODEL).
_REDIS_EMBED_KEY_PREFIX = "pw_embed:v1:"
_REDIS_EMBED_TTL_SEC = 604800  # 7 days


def _cache_put(key: str, vec: list[float]) -> None:
	"""Insert into the LRU cache, evicting the oldest entry if at capacity."""
	if key in _EMBED_CACHE:
		_EMBED_CACHE.move_to_end(key)
	else:
		if len(_EMBED_CACHE) >= _EMBED_CACHE_MAX:
			_EMBED_CACHE.popitem(last=False)  # evict LRU (oldest) entry
		_EMBED_CACHE[key] = vec


def _redis_get(key: str) -> "list[float] | None":
	"""Try to fetch a cached embedding vector from Redis. Returns None on any failure."""
	try:
		import frappe
		raw = frappe.cache().get_value(_REDIS_EMBED_KEY_PREFIX + key)
		if raw is not None and isinstance(raw, list):
			return raw
	except Exception:
		pass
	return None


def _redis_set(key: str, vec: "list[float]") -> None:
	"""Store an embedding vector in Redis with TTL. Silent on failure."""
	try:
		import frappe
		frappe.cache().set_value(
			_REDIS_EMBED_KEY_PREFIX + key, vec, expires_in_sec=_REDIS_EMBED_TTL_SEC
		)
	except Exception:
		pass


def _hash_text(value: str) -> str:
	return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_embed_text(value: str) -> str:
	# Keep embeddings stable and small. Embeddings work well even with truncation.
	text = (value or "").strip()
	text = " ".join(text.split())
	return text[:600]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
	if not left or not right or len(left) != len(right):
		return 0.0

	dot = 0.0
	left_norm = 0.0
	right_norm = 0.0
	for a, b in zip(left, right):
		dot += a * b
		left_norm += a * a
		right_norm += b * b

	den = math.sqrt(left_norm) * math.sqrt(right_norm)
	if den <= 0:
		return 0.0
	return dot / den


def _get_openai_client() -> openai.OpenAI | None:
	try:
		config = APProcessorSettings.get_openai_config()
		api_key = (config or {}).get("api_key")
		if not api_key:
			return None
		return openai.OpenAI(api_key=api_key)
	except Exception:
		return None


def embed_texts(texts: Iterable[str], *, model: str = EMBEDDING_MODEL) -> list[list[float]]:
	"""
	Return embeddings for `texts` using OpenAI embeddings API.

	Notes:
	- Uses a per-worker in-memory cache keyed by hash(text).
	- Never throws; returns [] on failure so callers can safely fall back.
	"""
	text_list = [_normalize_embed_text(t) for t in texts]
	if not text_list:
		return []

	client = _get_openai_client()
	if not client:
		return []

	output: list[list[float] | None] = [None] * len(text_list)
	missing_inputs: list[str] = []
	missing_indices: list[int] = []

	for idx, text in enumerate(text_list):
		if not text:
			output[idx] = []
			continue
		key = _hash_text(text)
		if key in _EMBED_CACHE:
			_EMBED_CACHE.move_to_end(key)  # mark as recently used
			output[idx] = _EMBED_CACHE[key]
		else:
			# L2: check Redis before calling OpenAI
			redis_vec = _redis_get(key)
			if redis_vec:
				_cache_put(key, redis_vec)
				output[idx] = redis_vec
			else:
				missing_inputs.append(text)
				missing_indices.append(idx)

	if missing_inputs:
		try:
			resp = client.embeddings.create(model=model, input=missing_inputs)
			data = resp.data or []
			for local_idx, emb in enumerate(data):
				vec = list(emb.embedding or [])
				idx = missing_indices[local_idx]
				output[idx] = vec
				if missing_inputs[local_idx]:
					h = _hash_text(missing_inputs[local_idx])
					_cache_put(h, vec)
					_redis_set(h, vec)
		except Exception:
			return []

	return [vec or [] for vec in output]


def semantic_rank(query: str, candidates: list[dict], *, text_key: str = "text", top_k: int = 5) -> list[dict]:
	"""
	Rank `candidates` by semantic similarity between `query` and `candidate[text_key]`.

	Returns a new list of candidates with `semantic_score` added, sorted desc.
	Returns [] on failure (caller should fall back to lexical matching).
	"""
	if not query or not candidates:
		return []

	texts = [query]
	for candidate in candidates:
		texts.append(str(candidate.get(text_key) or ""))

	vectors = embed_texts(texts)
	if not vectors or len(vectors) != len(texts):
		return []

	query_vec = vectors[0]
	ranked = []
	for candidate, cand_vec in zip(candidates, vectors[1:]):
		score = _cosine_similarity(query_vec, cand_vec)
		entry = dict(candidate)
		entry["semantic_score"] = round(max(0.0, min(1.0, score)), 4)
		ranked.append(entry)

	ranked.sort(key=lambda row: float(row.get("semantic_score") or 0.0), reverse=True)
	return ranked[: max(1, int(top_k or 5))]


def clear_embedding_cache():
	"""Debug helper: clears both the per-worker in-memory cache and the Redis cache."""
	_EMBED_CACHE.clear()
	try:
		import frappe
		frappe.cache().delete_keys(_REDIS_EMBED_KEY_PREFIX + "*")
	except Exception:
		pass
