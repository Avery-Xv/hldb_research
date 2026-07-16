#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="${ROOT_DIR}/.venv/hldb"

if command -v conda >/dev/null 2>&1; then
  if conda env list | awk '{print $1}' | grep -qx "hldb"; then
    conda env update -n hldb -f "${ROOT_DIR}/environment.yml" --prune
  else
    conda env create -f "${ROOT_DIR}/environment.yml"
  fi
  conda run -n hldb python -m pip install -e "${ROOT_DIR}"

  cat <<MSG
hldb Conda environment is ready.

Activate it with:
  conda activate hldb

Validate it with:
  python -c "import pandas, polars, duckdb, pyarrow; print('ok')"
MSG
  exit 0
fi

python3 -m venv "${ENV_DIR}"
"${ENV_DIR}/bin/python" -m pip install --upgrade pip
"${ENV_DIR}/bin/python" -m pip install -r "${ROOT_DIR}/requirements.txt"
"${ENV_DIR}/bin/python" -m pip install -e "${ROOT_DIR}"

cat <<MSG
hldb venv environment is ready.

Activate it with:
  source .venv/hldb/bin/activate

Validate it with:
  python -c "import pandas, polars, duckdb, pyarrow; print('ok')"
MSG
