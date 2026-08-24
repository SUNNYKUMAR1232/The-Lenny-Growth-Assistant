#!/usr/bin/env bash
#
# Lenny Growth Assistant - one-command setup for macOS and Linux.
#
#   ./scripts/setup.sh                  # knowledge base of 25 episodes (minutes)
#   ./scripts/setup.sh --full           # all 303 episodes (long)
#   ./scripts/setup.sh --episodes 50    # pick your own size
#   ./scripts/setup.sh --force          # re-chunk + re-embed what is already indexed
#   ./scripts/setup.sh --skip-models    # Ollama already has the models pulled
#   ./scripts/setup.sh --skip-transcripts
#
# Idempotent by design: every step checks the current state before acting, so a
# re-run after a failure resumes rather than starting over.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TRANSCRIPT_REPO="https://github.com/ChatPRD/lennys-podcast-transcripts.git"
LLM_MODEL="llama3.1:8b"
EMBED_MODEL="nomic-embed-text"
OLLAMA_HOST_URL="http://localhost:11434"
# The backend runs in a container and reaches the host's Ollama by this name.
OLLAMA_DOCKER_URL="http://host.docker.internal:11434"

EPISODES=25
FORCE=0
SKIP_MODELS=0
SKIP_TRANSCRIPTS=0

if [ -t 1 ]; then
  C_B="\033[1m"; C_G="\033[32m"; C_Y="\033[33m"; C_R="\033[31m"; C_D="\033[2m"; C_N="\033[0m"
else
  C_B=""; C_G=""; C_Y=""; C_R=""; C_D=""; C_N=""
fi

step() { printf "\n${C_G}==>${C_N} ${C_B}%s${C_N}\n" "$1"; }
info() { printf "    %s\n" "$1"; }
warn() { printf "    ${C_Y}!${C_N} %s\n" "$1"; }
die()  { printf "\n${C_R}failed:${C_N} %s\n\n" "$1" >&2; exit 1; }

usage() { sed -n '3,13p' "${BASH_SOURCE[0]}" | cut -c3-; exit 0; }

while [ $# -gt 0 ]; do
  case "$1" in
    --full)             EPISODES=0 ;;
    --episodes)         EPISODES="${2:?--episodes needs a number}"; shift ;;
    --force)            FORCE=1 ;;
    --skip-models)      SKIP_MODELS=1 ;;
    --skip-transcripts) SKIP_TRANSCRIPTS=1 ;;
    -h|--help)          usage ;;
    *) die "unknown option: $1  (try --help)" ;;
  esac
  shift
done

# --------------------------------------------------------------- 1. preflight
step "Checking prerequisites"
command -v docker >/dev/null 2>&1 || die "docker not found - install Docker Desktop or the docker engine."
docker compose version >/dev/null 2>&1 || die "'docker compose' (v2) not available. Update Docker."
docker info >/dev/null 2>&1 || die "Docker is installed but not running. Start Docker and re-run."
command -v git >/dev/null 2>&1 || die "git not found - needed to fetch the transcript archive."
info "docker + compose + git ok"

if [ "$SKIP_MODELS" -eq 0 ]; then
  command -v ollama >/dev/null 2>&1 || die "ollama not found - install from https://ollama.com, or pass --skip-models and configure a cloud model in the UI."
  if ! curl -fsS -m 5 "$OLLAMA_HOST_URL/api/tags" >/dev/null 2>&1; then
    info "Ollama installed but not serving - starting it in the background"
    nohup ollama serve >/tmp/ollama-serve.log 2>&1 &
    for _ in $(seq 1 20); do
      curl -fsS -m 2 "$OLLAMA_HOST_URL/api/tags" >/dev/null 2>&1 && break
      sleep 1
    done
    curl -fsS -m 2 "$OLLAMA_HOST_URL/api/tags" >/dev/null 2>&1 \
      || die "could not reach Ollama at $OLLAMA_HOST_URL (log: /tmp/ollama-serve.log)"
  fi
  info "ollama reachable at $OLLAMA_HOST_URL"
fi

# ------------------------------------------------------------------- 2. .env
step "Writing .env"
if [ ! -f .env ]; then
  cp .env.example .env
  info "created .env from .env.example"
else
  info ".env exists - updating only the keys this script owns"
fi

set_env() {  # set_env KEY VALUE - upsert, preserving everything else
  local key="$1" val="$2" tmp
  tmp="$(mktemp)"
  if grep -qE "^[[:space:]]*${key}=" .env; then
    sed "s|^[[:space:]]*${key}=.*|${key}=${val}|" .env > "$tmp"
  else
    cat .env > "$tmp"
    printf '%s=%s\n' "$key" "$val" >> "$tmp"
  fi
  mv "$tmp" .env
}

# .env.example ships localhost, which is right for the no-Docker path and wrong
# for the container: compose substitutes this value in, so leaving it as
# localhost makes the backend look for Ollama inside its own container.
set_env OLLAMA_BASE_URL        "$OLLAMA_DOCKER_URL"
set_env OLLAMA_MODEL           "$LLM_MODEL"
set_env OLLAMA_EMBEDDING_MODEL "$EMBED_MODEL"
set_env LLM_PROVIDER           "ollama"
set_env EMBEDDING_PROVIDER     "ollama"
info "provider=ollama  model=$LLM_MODEL  embeddings=$EMBED_MODEL"

# ----------------------------------------------------------------- 3. models
if [ "$SKIP_MODELS" -eq 0 ]; then
  step "Pulling local models (~5 GB on first run)"
  for m in "$LLM_MODEL" "$EMBED_MODEL"; do
    if ollama list 2>/dev/null | awk '{print $1}' | sed 's/:latest$//' | grep -qx "${m%:latest}"; then
      info "$m already present"
    else
      info "pulling $m ..."
      ollama pull "$m" || die "ollama pull $m failed"
    fi
  done
else
  step "Skipping model pull (--skip-models)"
fi

# ------------------------------------------- 4. knowledge base: the transcripts
step "Setting up the knowledge base (transcripts)"
if [ "$SKIP_TRANSCRIPTS" -eq 1 ]; then
  info "skipped (--skip-transcripts)"
elif [ -d data/transcripts/episodes ] && [ -n "$(ls -A data/transcripts/episodes 2>/dev/null)" ]; then
  info "already present: $(ls data/transcripts/episodes | wc -l | tr -d ' ') episodes"
else
  info "cloning the transcript archive (~26 MB) ..."
  rm -rf data/transcripts/_archive
  mkdir -p data/transcripts
  git clone --depth 1 "$TRANSCRIPT_REPO" data/transcripts/_archive >/dev/null 2>&1 \
    || die "clone failed - check your network, or copy an existing archive into data/transcripts/episodes"
  mv data/transcripts/_archive/episodes data/transcripts/episodes
  rm -rf data/transcripts/_archive
  info "$(ls data/transcripts/episodes | wc -l | tr -d ' ') episodes on disk"
fi

# ------------------------------------------------------------------ 5. stack
step "Starting the stack (postgres + backend + frontend)"
docker compose up --build -d || die "docker compose up failed"

printf "    waiting for the backend "
HEALTH=""
for _ in $(seq 1 60); do
  HEALTH="$(curl -fsS -m 3 http://localhost:8000/health 2>/dev/null || true)"
  case "$HEALTH" in *'"status":"ok"'*) break ;; esac
  printf "."
  sleep 3
done
printf "\n"
case "$HEALTH" in
  *'"status":"ok"'*) info "backend healthy" ;;
  *) die "backend did not become healthy. Inspect with: docker compose logs backend" ;;
esac

# ----------------------------------------------- 6. knowledge base: the index
step "Indexing the knowledge base"
INGEST_ARGS=""
[ "$EPISODES" -ne 0 ] && INGEST_ARGS="--limit $EPISODES"
[ "$FORCE" -eq 1 ] && INGEST_ARGS="$INGEST_ARGS --force"

if [ "$EPISODES" -eq 0 ]; then
  warn "indexing all 303 episodes - this embeds ~21,700 chunks and takes a while."
else
  info "indexing $EPISODES episodes (use --full for all 303)"
fi
# shellcheck disable=SC2086
docker compose exec -T backend python -m app.scripts.ingest $INGEST_ARGS \
  || die "ingestion failed. Inspect with: docker compose logs backend"

# ------------------------------------------------------------------ 7. verify
step "Verifying"
curl -fsS -m 10 http://localhost:8000/api/ingestion/stats | sed 's/^/    /'
printf "\n"
curl -fsS -m 10 http://localhost:8000/api/model | sed 's/^/    /'
printf "\n"

printf "\n${C_G}Setup complete.${C_N}\n\n"
printf "  frontend   http://localhost:3000\n"
printf "  backend    http://localhost:8000/health\n\n"
printf "  ${C_D}Amber model badge means Ollama is unreachable from the container:\n"
printf "  check 'ollama list' and that OLLAMA_BASE_URL uses host.docker.internal.${C_N}\n\n"
