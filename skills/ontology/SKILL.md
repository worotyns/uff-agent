# Ontology – Typed Knowledge Graph

Typed knowledge graph for structured agent memory. Create, query, link, and traverse
entities with schema validation.

## Tools
- `entity_create(type, properties)` – create a new entity
- `entity_get(id)` – get entity details + relations
- `entity_query(type, filters)` – search entities
- `entity_relate(from, relation, to, properties)` – link two entities
- `entity_graph(id, depth)` – traverse the graph
- `entity_update(id, properties)` – update entity properties
- `entity_remove(id)` – delete an entity

## Storage
`memory/entities/graph.jsonl` (append-only)
`memory/entities/schema.yaml` (optional type constraints)
