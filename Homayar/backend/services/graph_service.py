"""Graph intelligence service - entity exploration and neighborhood queries."""
from typing import Optional
from backend.database.neo4j_connection import Neo4jConnection


def get_entities(label: str = "", search: str = "") -> list:
    where = ""
    params = {}
    conditions = []
    if label and label != "All":
        # Validate label against allowed node labels to prevent injection
        allowed_labels = {"CallAgent", "Call", "Transcript", "QCReport", "ProblemDetection", "BusinessInsight"}
        safe_label = label if label in allowed_labels else ""
        if safe_label:
            conditions.append(f"n:{safe_label}")
    if search:
        conditions.append("n.name CONTAINS $search")
        params["search"] = search
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    query = f"""
    MATCH (n)
    {where}
    RETURN labels(n) AS labels, n.id AS id, n.name AS name,
           n.status AS status, n AS props
    ORDER BY n.name
    LIMIT 100
    """
    results = Neo4jConnection.execute_query(query, params)
    entities = []
    for r in results:
        labels = r["labels"]
        label_str = labels[0] if labels else "Unknown"
        props = dict(r["props"])
        props.pop("id", None)
        props.pop("name", None)
        entities.append({
            "id": r["id"],
            "label": label_str,
            "name": r["name"],
            "status": r.get("status"),
            "properties": {k: str(v) for k, v in props.items()},
        })
    return entities


def get_entity_detail(node_id: str) -> Optional[dict]:
    query = """
    MATCH (n {id: $id})
    OPTIONAL MATCH (n)-[r]-(m)
    RETURN n, labels(n) AS labels,
           collect({
               rel_type: type(r),
               target_id: m.id,
               target_name: m.name,
               target_labels: labels(m)
           }) AS connections
    """
    results = Neo4jConnection.execute_query(query, {"id": node_id})
    if not results:
        return None
    r = results[0]
    node = dict(r["n"])
    return {
        "id": node_id,
        "labels": r["labels"],
        "properties": {k: str(v) for k, v in node.items()},
        "connections": [c for c in r["connections"] if c["target_id"] is not None],
    }


def get_neighborhood(node_id: str, depth: int = 1) -> dict:
    # Clamp depth to safe range
    safe_depth = max(1, min(int(depth), 3))
    query = f"""
    MATCH path = (center {{id: $id}})-[r*1..{safe_depth}]-(connected)
    RETURN center, relationships(path) AS rels, nodes(path) AS nodes
    LIMIT 50
    """
    results = Neo4jConnection.execute_query(query, {"id": node_id})
    nodes_map = {}
    edges = []
    for r in results:
        for node in r["nodes"]:
            node_dict = dict(node)
            nid = node_dict.get("id")
            if nid and nid not in nodes_map:
                labels = []
                nodes_map[nid] = {
                    "id": nid,
                    "label": labels[0] if labels else "Unknown",
                    "name": node_dict.get("name", nid),
                    "properties": {k: str(v) for k, v in node_dict.items()},
                }
        for rel in r["rels"]:
            start = dict(rel.start_node)
            end = dict(rel.end_node)
            edges.append({
                "source": start.get("id"),
                "target": end.get("id"),
                "type": rel.type,
            })
    return {"nodes": list(nodes_map.values()), "edges": edges}


def get_graph_stats() -> dict:
    query = """
    MATCH (n)
    RETURN labels(n)[0] AS label, count(*) AS count
    ORDER BY count DESC
    """
    results = Neo4jConnection.execute_query(query)
    return {r["label"]: r["count"] for r in results if r["label"]}
