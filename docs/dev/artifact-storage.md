---
title: "Artifact storage"
summary: Where files that agents produce are stored, how ARTIFACT_SERVICE picks the backend, and how each backend lays out keys and versions.
audience: dev
order: 240
covers:
  - shared/utils/artifacts/local_folder_artifact_service.py
  - shared/utils/artifacts/s3_artifact_service.py
  - shared/utils/artifacts/supabase_artifact_service.py
---

# Artifact storage

An artifact is a file an agent saves during a conversation: raw bytes plus a MIME
type, wrapped in a `google.genai` `types.Part`. Artifacts are saved through the ADK
artifact service API (`save_artifact`, `load_artifact`, `list_versions`, ...) for an
app, a user and a session, and every save creates a new numbered version.

Two tools save artifacts today:

- the image tool (`shared/utils/tools/image_tools.py`) saves each generated image as
  `generated_image_<timestamp>.<ext>`;
- browser automation (`shared/utils/tools/browser_tools.py`) saves screenshots as
  `browser_screenshot_<timestamp>.png`.

The image MCP server path (`image_mcp_protocol_handler.py`) passes a context whose
`save_artifact` raises, so images generated through it are not stored.

Artifacts are read back through the agent server's
`/apps/{app}/users/{user}/sessions/{session}/artifacts` routes (ADK's own, or the
LangGraph runtime's emulation in `shared/utils/langgraph/api.py`). The dashboard
widget reaches them through `/api/widget/artifacts/...` in `server/widget_routes.py`,
which proxies to those routes for the artifact's owner or an admin.

## Choosing a backend

`ARTIFACT_SERVICE` (read by `settings.artifact_service()`, lowercased) picks the
backend. Both runtimes use the same rule: `adk_main.py` for ADK and
`_create_service()` in `shared/utils/langgraph/artifact_adapter.py` for LangGraph.

| `ARTIFACT_SERVICE` | Service | Where files go |
|---|---|---|
| `local_folder` | `LocalFolderArtifactService` | `artifacts/` at the repository root |
| `s3` | `S3ArtifactService` | an S3 bucket, or an S3-compatible store such as MinIO |
| `supabase` | `SupabaseArtifactService` | a Supabase Storage bucket |
| unset, `none`, or any other value | ADK's `InMemoryArtifactService` | process memory |

Any value the code does not recognise, including a typo, falls back to memory
without an error. In-memory artifacts are lost on restart and are not shared
between processes. The default is `none`; `docker-compose.yml` passes
`${ARTIFACT_SERVICE:-local_folder}`, so containers default to `local_folder`. The
variables each backend reads are listed in the
[configuration reference](../reference/configuration.md).

## Keys and versions

All three backends use the same layout:

```text
<app_name>/<user_id>/<session_id>/<filename>/<version>
<app_name>/<user_id>/user/<filename>/<version>      # filenames starting with "user:"
```

A filename that starts with `user:` is user-scoped: it is stored outside any
session, under a `user` folder. Each version is a separate file or object whose
name is the version number.

To save, a service lists the existing versions and writes `max + 1`, starting at
`0`. Listing and writing are separate steps, so two concurrent saves of the same
filename can pick the same number, and the later write replaces the earlier one.
`load_artifact` without a version loads the highest one. `delete_artifact` removes
every version. None of the three services stores the `custom_metadata` argument of
`save_artifact`.

## Local folder

`ARTIFACT_SERVICE=local_folder` stores files under `artifacts/` next to
`adk_main.py`. The path is fixed and created at startup; no variable changes it.
`docker-compose.yml` mounts `./artifacts` as a volume so files survive a container
restart.

- Paths are built with `resolve_within_base`, so a filename such as `../../.env`
  cannot escape the base folder.
- For user-scoped files the `user:` prefix is removed from the folder name:
  `user:notes.txt` is stored as `<app>/<user>/user/notes.txt/<version>`.
- The MIME type is not stored. `load_artifact` guesses it from the filename and
  falls back to `text/plain`. The version metadata guesses it the same way but
  falls back to `application/octet-stream`.
- `create_time` is the file's modification time, and `canonical_uri` is the
  `file://` URI of the version file.
- `list_artifact_keys` walks the session and user folders. A filename that contains
  `/` becomes nested folders and is listed with its `/`. A user-scoped name hides a
  session name that is the same apart from the prefix.

## S3

`ARTIFACT_SERVICE=s3` uses boto3:

| Variable | Default | Use |
|---|---|---|
| `DISTRIBUTION_S3_BUCKET_NAME` | `test-bucket` | bucket name |
| `DISTRIBUTION_S3_ENDPOINT` | none | endpoint for S3-compatible storage (MinIO and similar) |

MATE does not pass credentials or a region to boto3, so they come from boto3's
usual chain: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`,
`~/.aws`, or an instance role.

- Keys follow the layout above, except that user-scoped keys keep the prefix:
  `<app>/<user>/user/user:notes.txt/<version>`.
- The part's MIME type is stored as the object's `ContentType` and returned by
  `load_artifact`. `create_time` is the object's `LastModified`.
- `canonical_uri` has the form `s3://<bucket>/.../<filename>/versions/<version>`.
  The `versions/` segment is not part of the real key.
- `list_artifact_keys` returns the fourth segment of each key. For a filename that
  contains `/` that is only its first component, and user-scoped names come back
  with their `user:` prefix.
- A missing bucket is logged and raised from `list_versions`, so saving fails.

The image tool and browser screenshots build a public link as
`<DISTRIBUTION_DOMAIN>/<app>/<user>/<session>/<filename>/<version>`, which assumes
something like a CloudFront distribution in front of the bucket. Without
`DISTRIBUTION_DOMAIN` no public link is built.

## Supabase

`ARTIFACT_SERVICE=supabase` uses Supabase Storage:

| Variable | Default | Use |
|---|---|---|
| `SUPABASE_URL` | none | project URL, required |
| `SUPABASE_KEY` | none | API key, required |
| `SUPABASE_BUCKET` | `artifacts` | bucket name |

Without `SUPABASE_URL` or `SUPABASE_KEY` the constructor raises `ValueError`. The
ADK runtime creates the service when `adk_main.py` is imported, so it fails to
start; the LangGraph runtime creates it on the first artifact call, so that call
fails. The `supabase` package is imported on first use; if it is missing that call
raises `RuntimeError`.

- Every key starts with `public/`: `public/<app>/<user>/<session>/<filename>/<version>`.
  The code comment says this matches a row-level security policy for anonymous
  access. User-scoped keys keep the `user:` prefix, as with S3.
- The upload sets the content type, but `load_artifact` does not read it back: it
  picks `image/png`, `image/jpeg` or `text/plain` from the file extension and
  `application/octet-stream` otherwise. The version metadata reads `mimetype` and
  `created_at` from Storage's file listing.
- `canonical_uri` has the form `supabase://<bucket>/public/.../<filename>/versions/<version>`;
  as with S3, `versions/` is not part of the real key.
- Load, list and delete errors are logged and swallowed. When listing fails, the
  service sees no versions and saves as version `0`; if that object already
  exists, the upload falls back to `update` and replaces it.

The image tool and browser screenshots link to
`<SUPABASE_URL>/storage/v1/object/public/<bucket>/public/...`, which only works if
the bucket is public.

### The shared bucket and `public/`

`SUPABASE_BUCKET` is read through `settings.supabase_bucket()` by both the artifact
service and the Supabase storage tools (`shared/utils/tools/supabase_tools.py`), so
both use the same bucket. Every user's chat artifacts sit under `public/` at
predictable paths. The storage tools therefore refuse any path whose first segment,
after normalisation, is `public`: an agent steered by a chat message must not be
able to read, overwrite or delete other users' artifacts (#180).

## Where the code is

| File | What it holds |
|---|---|
| `shared/utils/settings.py` | `artifact_service()` and `supabase_bucket()` |
| `adk_main.py` | registers the three services and picks one for the ADK runtime |
| `shared/utils/langgraph/artifact_adapter.py` | picks one for the LangGraph runtime and wraps it |
| `shared/utils/artifacts/local_folder_artifact_service.py` | local folder backend |
| `shared/utils/artifacts/s3_artifact_service.py` | S3 backend |
| `shared/utils/artifacts/supabase_artifact_service.py` | Supabase backend |
| `shared/utils/tools/image_tools.py` | saves generated images and builds their public links |
| `shared/utils/tools/supabase_tools.py` | Supabase storage tools and the `public/` refusal |
