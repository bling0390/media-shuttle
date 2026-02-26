# specs

API and event contracts module.

This module does not run a long-lived service. It provides shared contract files for `media-shuttle-api`, `media-shuttle-core`, and adapters.

## 1. Included files

- `openapi.yaml`: REST API contract.
- `events/task.created.v1.schema.json`: queue event schema for new parse tasks.
- `events/task.status.v1.schema.json`: queue event schema for task status updates.

## 2. Standalone checks

Env file convention:

```bash
cd specs
cp .env.example .env
```

`specs` does not require runtime environment variables, so `.env.example` is intentionally empty of variable definitions.

Syntax check JSON schema files:

```bash
cd specs
python3 -m json.tool events/task.created.v1.schema.json >/dev/null
python3 -m json.tool events/task.status.v1.schema.json >/dev/null
```

Run repository contract tests (recommended):

```bash
cd ..
python3 -m unittest tests.test_specs_contract_files
```

## 3. Optional local preview

If you want a local OpenAPI UI:

```bash
docker run --rm -p 8081:8080 \
  -e SWAGGER_JSON=/specs/openapi.yaml \
  -v "$(pwd)/specs:/specs" \
  swaggerapi/swagger-ui
```
