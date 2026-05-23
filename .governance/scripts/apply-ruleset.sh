#!/usr/bin/env bash
set -euo pipefail

REPO="robertoandr/it-governance-dashboard"

PAYLOAD=$(cat <<'EOF'
{
  "name": "Protect main branch",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "include": ["refs/heads/main"],
      "exclude": []
    }
  },
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "required_linear_history"}
  ]
}
EOF
)

echo "🛡️  Aplicando ruleset..."
echo "$PAYLOAD" | gh api --method POST \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "/repos/$REPO/rulesets" \
  --input -

echo ""
echo "✅ Validando..."
gh api "/repos/$REPO/rulesets" --jq '.[] | {id, name, enforcement, target}'
