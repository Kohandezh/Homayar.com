import { useState, useCallback } from "react";
import {
  ReactFlow,
  Controls,
  MiniMap,
  Background,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  Handle,
  Position,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Search, Layers, Cpu, Radio, AlertTriangle, type LucideIcon } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import api from "@/lib/api";
import type { Entity, EntityDetail } from "@/types";

interface LabelConfig {
  bg: string;
  border: string;
  icon: LucideIcon;
}

const labelColors: Record<string, LabelConfig> = {
  Agent: { bg: "rgba(99,102,241,0.15)", border: "#6366F1", icon: Cpu },
  Stream: { bg: "rgba(34,197,94,0.15)", border: "#22C55E", icon: Radio },
  Model: { bg: "rgba(139,92,246,0.15)", border: "#8B5CF6", icon: Layers },
  Incident: { bg: "rgba(239,68,68,0.15)", border: "#EF4444", icon: AlertTriangle },
};

function CustomNode({ data }: { data: { label: string; name: string; status?: string } }) {
  const config = labelColors[data.label] || labelColors.Agent;
  const Icon = config.icon;
  return (
    <div
      className="px-3 py-2 rounded-xl min-w-[120px]"
      style={{ background: config.bg, border: `1px solid ${config.border}40` }}
    >
      <Handle type="target" position={Position.Top} style={{ background: config.border }} />
      <div className="flex items-center gap-2">
        <Icon size={14} style={{ color: config.border }} />
        <span className="text-xs font-medium text-white">{data.name}</span>
      </div>
      <div className="text-[10px] text-text-muted mt-0.5">{data.label}</div>
      {data.status && (
        <div className="flex items-center gap-1 mt-1">
          <span
            className={`w-1.5 h-1.5 rounded-full ${
              data.status === "active" || data.status === "live"
                ? "bg-success"
                : data.status === "error"
                ? "bg-error"
                : "bg-warning"
            }`}
          />
          <span className="text-[10px] text-text-muted">{data.status}</span>
        </div>
      )}
      <Handle type="source" position={Position.Bottom} style={{ background: config.border }} />
    </div>
  );
}

const nodeTypes = { custom: CustomNode };

function getMiniMapNodeColor(node: Node) {
  const label = (node.data as { label?: unknown }).label;
  return typeof label === "string" ? labelColors[label]?.border || "#666" : "#666";
}

export default function GraphPage() {
  const [filter, setFilter] = useState("All");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<EntityDetail | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  const { data: entities = [] } = useQuery<Entity[]>({
    queryKey: ["graph", "entities", filter, search],
    queryFn: async () => {
      const res = await api.get("/graph/entities", { params: { label: filter === "All" ? "" : filter, search } });
      return res.data;
    },
  });

  const loadNeighborhood = useCallback(
    async (nodeId: string) => {
      try {
        const res = await api.get(`/graph/neighborhood/${nodeId}`, { params: { depth: 1 } });
        const data = res.data;
        const newNodes: Node[] = data.nodes.map((n: { id: string; label: string; name: string; status?: string; properties?: Record<string, string> }) => ({
          id: n.id,
          type: "custom",
          position: { x: Math.random() * 600, y: Math.random() * 400 },
          data: { label: n.label, name: n.name, status: n.properties?.status },
        }));
        const newEdges: Edge[] = data.edges.map((e: { source: string; target: string; type: string }) => ({
          id: `${e.source}-${e.target}`,
          source: e.source,
          target: e.target,
          animated: true,
          style: { stroke: "rgba(255,255,255,0.15)", strokeWidth: 1 },
        }));
        setNodes(newNodes);
        setEdges(newEdges);
      } catch {
        // ignore
      }
    },
    [setNodes, setEdges]
  );

  const inspectEntity = async (nodeId: string) => {
    try {
      const res = await api.get(`/graph/entity/${nodeId}`);
      setSelected(res.data);
    } catch {
      // ignore
    }
  };

  return (
    <div className="grid grid-cols-[260px_1fr_320px] gap-0 h-[calc(100vh-56px-48px)] -m-6">
      {/* Left: Entity Explorer */}
      <div className="bg-bg-secondary border-r border-border-subtle p-4 overflow-y-auto">
        <h3 className="text-sm font-semibold text-text-primary mb-3">Entities</h3>
        <div className="flex items-center gap-2 bg-bg-elevated rounded-lg px-3 py-1.5 mb-3">
          <Search size={14} className="text-text-muted" />
          <input
            type="text"
            placeholder="Search..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="bg-transparent text-sm text-text-primary placeholder:text-text-muted outline-none flex-1"
          />
        </div>
        <div className="flex gap-1 mb-3 flex-wrap">
          {["All", "Agent", "Stream", "Model", "Incident"].map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-2 py-1 rounded text-xs transition-colors ${
                filter === f ? "bg-accent text-white" : "bg-bg-elevated text-text-secondary hover:text-text-primary"
              }`}
            >
              {f}
            </button>
          ))}
        </div>
        <div className="space-y-1">
          {entities.map((e) => (
            <button
              key={e.id}
              onClick={() => {
                loadNeighborhood(e.id);
                inspectEntity(e.id);
              }}
              className="w-full text-left px-2 py-1.5 rounded-lg hover:bg-bg-elevated transition-colors flex items-center gap-2"
            >
              <span
                className="w-2 h-2 rounded-full"
                style={{ background: labelColors[e.label]?.border || "#666" }}
              />
              <span className="text-sm text-text-primary truncate">{e.name}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Center: Graph Canvas */}
      <div className="bg-bg-primary">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={(_, node) => inspectEntity(node.id)}
          nodeTypes={nodeTypes}
          fitView
          proOptions={{ hideAttribution: true }}
        >
          <Controls
            style={{ background: "#0B0F17", border: "1px solid rgba(255,255,255,0.08)", borderRadius: "8px" }}
          />
          <MiniMap
            style={{ background: "#0B0F17", border: "1px solid rgba(255,255,255,0.08)" }}
            nodeColor={getMiniMapNodeColor}
          />
          <Background color="rgba(255,255,255,0.03)" gap={20} />
        </ReactFlow>
      </div>

      {/* Right: Inspector */}
      <div className="bg-bg-secondary border-l border-border-subtle p-4 overflow-y-auto">
        <h3 className="text-sm font-semibold text-text-primary mb-3">Inspector</h3>
        {!selected ? (
          <p className="text-xs text-text-muted">Click an entity to inspect</p>
        ) : (
          <div className="space-y-4">
            <div>
              <div className="flex items-center gap-2 mb-1">
                <span
                  className="px-2 py-0.5 rounded text-[10px] font-medium"
                  style={{
                    background: labelColors[selected.labels?.[0] ?? ""]?.bg || "rgba(255,255,255,0.1)",
                    color: labelColors[selected.labels?.[0] ?? ""]?.border || "#fff",
                  }}
                >
                  {selected.labels?.[0] || "Unknown"}
                </span>
              </div>
              <p className="text-base font-semibold text-text-primary">
                {selected.properties?.name || selected.id}
              </p>
            </div>

            <div>
              <h4 className="text-xs text-text-muted mb-2 uppercase tracking-wider">Properties</h4>
              <div className="space-y-1.5">
                {Object.entries(selected.properties || {})
                  .filter(([k]) => !["id", "name"].includes(k))
                  .slice(0, 10)
                  .map(([key, val]) => (
                    <div key={key} className="flex justify-between text-xs">
                      <span className="text-text-muted">{key}</span>
                      <span className="text-text-primary font-mono">{val}</span>
                    </div>
                  ))}
              </div>
            </div>

            {selected.connections && selected.connections.length > 0 && (
              <div>
                <h4 className="text-xs text-text-muted mb-2 uppercase tracking-wider">Connections</h4>
                <div className="space-y-1">
                  {selected.connections.map((c, i) => (
                    <button
                      key={i}
                      onClick={() => {
                        loadNeighborhood(c.target_id);
                        inspectEntity(c.target_id);
                      }}
                      className="w-full text-left px-2 py-1.5 rounded-lg hover:bg-bg-elevated transition-colors"
                    >
                      <span className="text-xs text-text-primary">{c.target_name}</span>
                      <span className="block text-[10px] text-text-muted">
                        {c.rel_type} → {c.target_labels?.[0]}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
