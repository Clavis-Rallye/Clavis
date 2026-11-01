#!/usr/bin/env bash
# Fetch the files of the API documentation pages /api/docs (Swagger UI) and
# /api/redoc (ReDoc) from the npm registry into src/backend/app/static/api-docs/.
#
# The API serves these files itself (src/backend/app/api_docs.py). FastAPI's
# stock pages load them from cdn.jsdelivr.net, which the API's content policy
# blocks, and Clavis makes no request to a third party at runtime.
#
# Usage: scripts/update-api-docs-assets.sh
# Needs: npm, tar, perl. npm checks each package against the registry's
# integrity hash. Pin a version here, then run the script and commit the result.
# Pick a release that is at least two weeks old.

set -euo pipefail

SWAGGER_UI_VERSION="5.34.1"
REDOC_VERSION="2.5.4"

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." > /dev/null 2>&1 && pwd)"
DEST="$ROOT/src/backend/app/static/api-docs"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

(cd "$work" && npm pack --silent "swagger-ui-dist@$SWAGGER_UI_VERSION" "redoc@$REDOC_VERSION" > /dev/null)
mkdir -p "$work/swagger-ui" "$work/redoc"
tar -xzf "$work/swagger-ui-dist-$SWAGGER_UI_VERSION.tgz" -C "$work/swagger-ui"
tar -xzf "$work/redoc-$REDOC_VERSION.tgz" -C "$work/redoc"

rm -rf "$DEST/swagger-ui" "$DEST/redoc"
mkdir -p "$DEST/swagger-ui" "$DEST/redoc"

# Swagger UI: Apache-2.0, LICENSE and NOTICE travel with the files.
for file in swagger-ui-bundle.js swagger-ui-bundle.js.LICENSE.txt swagger-ui.css LICENSE NOTICE; do
  cp "$work/swagger-ui/package/$file" "$DEST/swagger-ui/"
done

# ReDoc: MIT. The standalone bundle carries its search worker inline, with a
# licence file of its own.
cp "$work/redoc/package/LICENSE" "$DEST/redoc/"
cp "$work/redoc/package/bundles/redoc.standalone.js" "$work/redoc/package/bundles/redoc.standalone.js.LICENSE.txt" "$DEST/redoc/"
cp "$work/redoc/package/bundles/"*.worker.js.LICENSE.txt "$DEST/redoc/"

# The one change to a fetched file: ReDoc 2 shows the Redocly logo from
# cdn.redoc.ly in its menu, and no option turns it off. An empty src loads
# nothing, and ReDoc then hides the logo. The link text "API docs by Redocly"
# stays.
logo='src:"https://cdn.redoc.ly/redoc/logo-mini.svg"'
if ! grep -qF "$logo" "$DEST/redoc/redoc.standalone.js"; then
  echo "The ReDoc logo address is not where it was. Check the patch in $0." >&2
  exit 1
fi
perl -pi -e 's#\Qsrc:"https://cdn.redoc.ly/redoc/logo-mini.svg"\E#src:""#g' "$DEST/redoc/redoc.standalone.js"

cat > "$DEST/VERSIONS.txt" << EOF
Fetched by scripts/update-api-docs-assets.sh from registry.npmjs.org.
swagger-ui-dist $SWAGGER_UI_VERSION (Apache-2.0): swagger-ui/
redoc $REDOC_VERSION (MIT): redoc/, with the Redocly logo address emptied
EOF

echo "API docs assets: swagger-ui-dist $SWAGGER_UI_VERSION, redoc $REDOC_VERSION in $DEST"
