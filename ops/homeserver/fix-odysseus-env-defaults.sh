#!/usr/bin/env bash
set -euo pipefail

cd /opt/odysseus

add_if_missing() {
  local key="$1"
  local value="$2"
  if ! grep -q "^${key}=" .env; then
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

add_if_missing LLM_HOST ollama
add_if_missing LLM_HOSTS ollama
add_if_missing OPENAI_API_KEY ""
add_if_missing OLLAMA_BASE_URL http://ollama:11434
add_if_missing RESEARCH_LLM_ENDPOINT ""
add_if_missing HF_TOKEN ""
add_if_missing HUGGING_FACE_HUB_TOKEN ""
add_if_missing INSTALL_OPTIONAL false
add_if_missing INSTALL_OFFICE false
add_if_missing EMBEDDING_URL ""
add_if_missing EMBEDDING_MODEL ""
add_if_missing EMBEDDING_API_KEY ""
add_if_missing FASTEMBED_MODEL sentence-transformers/all-MiniLM-L6-v2
add_if_missing FASTEMBED_CACHE_PATH ""
add_if_missing CLEANUP_INTERVAL_HOURS 24
add_if_missing ODYSSEUS_INPROCESS_POLLERS 1
add_if_missing ODYSSEUS_INPROCESS_TASKS 1
add_if_missing ODYSSEUS_SCRIPT_HOST localhost
add_if_missing ODYSSEUS_CHAT_UPLOAD_MAX_BYTES 10485760
add_if_missing ODYSSEUS_GALLERY_UPLOAD_MAX_BYTES 104857600
add_if_missing ODYSSEUS_GALLERY_TRANSFORM_UPLOAD_MAX_BYTES 26214400
add_if_missing ODYSSEUS_MEMORY_IMPORT_MAX_BYTES 10485760
add_if_missing ODYSSEUS_PERSONAL_UPLOAD_MAX_BYTES 26214400
add_if_missing ODYSSEUS_EMAIL_COMPOSE_UPLOAD_MAX_BYTES 26214400
add_if_missing ODYSSEUS_STT_MAX_AUDIO_BYTES 26214400
add_if_missing ODYSSEUS_ICS_MAX_BYTES 10485760
add_if_missing ODYSSEUS_OBSIDIAN_SOMT_ENABLED true
add_if_missing ODYSSEUS_OBSIDIAN_FRESHNESS_GATE_ENABLED true
add_if_missing ODYSSEUS_OBSIDIAN_HYBRID_RETRIEVAL_ENABLED false
add_if_missing ODYSSEUS_OBSIDIAN_MEMORY_TREE_UI_ENABLED true
add_if_missing ODYSSEUS_OBSIDIAN_RAPTOR_ENABLED false
add_if_missing ODYSSEUS_OBSIDIAN_RAPTOR_REBUILD_ENABLED false
add_if_missing DATA_BRAVE_API_KEY ""
add_if_missing GOOGLE_API_KEY ""
add_if_missing GOOGLE_PSE_CX ""
add_if_missing TAVILY_API_KEY ""
add_if_missing SERPER_API_KEY ""

chmod 600 .env
echo "Odysseus .env defaults verified."
