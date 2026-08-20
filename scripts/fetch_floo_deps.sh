#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
deps_dir="${repo_root}/rtl/FlooNoC/deps"

mkdir -p "${deps_dir}"

if [[ ! -d "${deps_dir}/common_cells/.git" ]]; then
    git clone https://github.com/pulp-platform/common_cells.git \
        "${deps_dir}/common_cells"
fi
git -C "${deps_dir}/common_cells" checkout \
    63b7c50d43e462b59506f69d341ff1e40202866d

if [[ ! -d "${deps_dir}/axi/.git" ]]; then
    git clone https://github.com/pulp-platform/axi.git "${deps_dir}/axi"
fi
git -C "${deps_dir}/axi" checkout \
    0ccc838fe06aeeb857eb83c6be9a915c4bf99566

if [[ ! -d "${deps_dir}/tech_cells_generic/.git" ]]; then
    git clone https://github.com/pulp-platform/tech_cells_generic.git \
        "${deps_dir}/tech_cells_generic"
fi
git -C "${deps_dir}/tech_cells_generic" checkout \
    3a3de73632a06826b1bd9c65a0a2e92b32016845
