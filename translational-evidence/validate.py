"""Standard-library-only JSONL schema-sanity checker for the TE track.

This deliberately does NOT depend on the ``jsonschema`` package. It implements a
pragmatic subset of JSON Schema validation:

- every declared ``required`` key must be present AND non-null;
- for properties that appear and are declared in ``properties``, the JSON type
  is checked against the declared type(s) (a type list passes if the value
  matches any member; ``integer`` is accepted where ``number`` is allowed);
- ``enum`` membership is checked when declared;
- validation recurses one level into declared object properties and array item
  schemas;
- ``additionalProperties`` are allowed and NOT type-checked (schemas set it
  true).

Usage:

    python3 translational-evidence/validate.py                # all known files
    python3 translational-evidence/validate.py path/to/file.jsonl [more.jsonl]

Exit code is 0 when every present file passes, 1 otherwise. Missing output files
are reported as SKIP, not errors, so partial pipeline runs still validate
cleanly.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402  (import after sys.path bootstrap)

import json  # noqa: E402


# Processed-dir outputs owned by this track, mapped to their schema base name.
FILE_TO_SCHEMA = {
    common.PROCESSED_DIR / "gwas_associations.jsonl": "gwas_association",
    common.PROCESSED_DIR / "genes.jsonl": "gene",
    common.PROCESSED_DIR / "pathways.jsonl": "pathway",
    common.PROCESSED_DIR / "trials.jsonl": "trial",
    common.PROCESSED_DIR / "target_evidence.jsonl": "target_evidence",
    common.PROCESSED_DIR / "functional_links.jsonl": "functional_link",
    common.PROCESSED_DIR / "entity_metrics.jsonl": "entity_metric",
}

# Generated evidence-graph exports (under data/exports/graph). These are
# gitignored build products, so they are validated only when present.
GRAPH_EXPORT_DIR = common.REPO_ROOT / "data" / "exports" / "graph"

# Optional shared cross-track output (only validated if it exists).
OPTIONAL_FILE_TO_SCHEMA = {
    common.SHARED_PROCESSED_DIR / "topic_evidence_links.jsonl":
        "topic_evidence_link",
    common.SHARED_PROCESSED_DIR / "topic_evidence_rollup.jsonl":
        "topic_evidence_rollup",
    # Same shape as the topic bridge, but keyed to Track A's Theme Atlas (45
    # embedding themes). Reuses the topic_evidence_* schemas.
    common.SHARED_PROCESSED_DIR / "atlas_evidence_links.jsonl":
        "topic_evidence_link",
    common.SHARED_PROCESSED_DIR / "atlas_evidence_rollup.jsonl":
        "topic_evidence_rollup",
    GRAPH_EXPORT_DIR / "nodes.jsonl": "evidence_node",
    GRAPH_EXPORT_DIR / "edges.jsonl": "evidence_edge",
}

MAX_EXAMPLE_ERRORS = 5

# JSON-Schema type name -> predicate over a Python value.
_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}


def _load_schema(schema_base):
    """Load a schema JSON document by its base name."""
    path = common.SCHEMA_DIR / (schema_base + ".schema.json")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _type_names(schema):
    """Return the declared type(s) of a schema node as a list of strings."""
    declared = schema.get("type")
    if declared is None:
        return []
    if isinstance(declared, list):
        return list(declared)
    return [declared]


def _matches_type(value, type_names):
    """True if value matches any declared type (integer counts as number)."""
    if not type_names:
        # No declared type -> nothing to check.
        return True
    for tname in type_names:
        check = _TYPE_CHECKS.get(tname)
        if check is None:
            # Unknown type keyword: don't fail on it.
            return True
        if check(value):
            return True
        # Accept an integer value where a number is allowed.
        if tname == "number" and _TYPE_CHECKS["integer"](value):
            return True
    return False


def _validate_value(value, schema, path_label, errors):
    """Validate a single value against a schema node; append error strings.

    Recurses ONE level into object properties and array item schemas.
    """
    type_names = _type_names(schema)

    if not _matches_type(value, type_names):
        errors.append(
            "%s: expected type %s but got %s"
            % (path_label, type_names, _py_type_name(value))
        )
        # If the type is already wrong, deeper checks are not meaningful.
        return

    enum = schema.get("enum")
    if enum is not None and value not in enum:
        errors.append("%s: value %r not in enum %r" % (path_label, value, enum))

    # Recurse into declared object properties (one level).
    if isinstance(value, dict):
        props = schema.get("properties")
        if isinstance(props, dict):
            for key, subschema in props.items():
                if key in value and value[key] is not None:
                    _validate_value(
                        value[key], subschema,
                        "%s.%s" % (path_label, key), errors,
                    )

    # Recurse into array item schema (one level).
    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for idx, item in enumerate(value):
                if item is not None:
                    _validate_value(
                        item, items,
                        "%s[%d]" % (path_label, idx), errors,
                    )


def _py_type_name(value):
    """Human-friendly JSON-ish type name for a Python value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _validate_record(record, schema):
    """Validate one top-level record; return a list of error strings."""
    errors = []

    if not isinstance(record, dict):
        return ["record is not a JSON object (got %s)"
                % _py_type_name(record)]

    # Required keys must be present and non-null.
    for key in schema.get("required", []):
        if key not in record:
            errors.append("missing required key '%s'" % key)
        elif record[key] is None:
            errors.append("required key '%s' is null" % key)

    # Type/enum checks on declared, present properties.
    props = schema.get("properties", {})
    for key, subschema in props.items():
        if key in record and record[key] is not None:
            _validate_value(record[key], subschema, key, errors)

    return errors


def validate_file(path, schema_base):
    """Validate one JSONL file; return (n_records, list_of_(line, error))."""
    schema = _load_schema(schema_base)
    n_records = 0
    problems = []  # list of (line_number, error_message)

    with pathlib.Path(path).open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as err:
                problems.append((lineno, "JSON parse error: %s" % err))
                continue
            n_records += 1
            for err in _validate_record(record, schema):
                problems.append((lineno, err))

    return n_records, problems


def _resolve_targets(argv):
    """Build the ordered list of (path, schema_base) to validate."""
    if argv:
        targets = []
        combined = dict(FILE_TO_SCHEMA)
        combined.update(OPTIONAL_FILE_TO_SCHEMA)
        # Index known files by their basename for convenient lookup.
        by_name = {p.name: (p, s) for p, s in combined.items()}
        for arg in argv:
            p = pathlib.Path(arg)
            if p.name in by_name:
                known_path, schema_base = by_name[p.name]
                # Prefer the explicit path the user passed.
                targets.append((p if p.is_absolute() or p.exists()
                                else known_path, schema_base))
            else:
                common.log("unknown file (no schema mapping): %s" % arg)
                targets.append((p, None))
        return targets

    # No args: all known track files, then optional shared file.
    targets = list(FILE_TO_SCHEMA.items())
    targets.extend(OPTIONAL_FILE_TO_SCHEMA.items())
    return targets


def main(argv):
    targets = _resolve_targets(argv)
    any_errors = False

    for path, schema_base in targets:
        path = pathlib.Path(path)
        rel = _display_path(path)

        if schema_base is None:
            print("%s: ERROR (no schema mapping)" % rel)
            any_errors = True
            continue

        if not path.exists():
            print("%s: SKIP (not built yet)" % rel)
            continue

        n_records, problems = validate_file(path, schema_base)
        if not problems:
            print("%s: %d records, OK" % (rel, n_records))
        else:
            any_errors = True
            print("%s: %d records, %d errors" % (rel, n_records, len(problems)))
            for lineno, err in problems[:MAX_EXAMPLE_ERRORS]:
                print("    line %d: %s" % (lineno, err))
            if len(problems) > MAX_EXAMPLE_ERRORS:
                print("    ... and %d more" % (len(problems) - MAX_EXAMPLE_ERRORS))

    return 1 if any_errors else 0


def _display_path(path):
    """Show a repo-relative path when possible, else the raw path."""
    try:
        return str(path.resolve().relative_to(common.REPO_ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
