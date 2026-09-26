import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Users, Plus, Pencil, Trash2, X } from "lucide-react";
import api from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { User, UserCreate, UserRole, UserUpdate } from "@/types/user";

export default function UsersPage() {
  const qc = useQueryClient();
  const { user: currentUser } = useAuth();
  const [showCreate, setShowCreate] = useState(false);
  const [editUser, setEditUser] = useState<User | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<User | null>(null);

  const { data: users = [], isLoading } = useQuery<User[]>({
    queryKey: ["users"],
    queryFn: async () => (await api.get("/auth/users")).data,
  });

  const createMut = useMutation({
    mutationFn: async (data: UserCreate) => (await api.post("/auth/users", data)).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["users"] }); setShowCreate(false); },
  });

  const updateMut = useMutation({
    mutationFn: async ({ id, data }: { id: string; data: UserUpdate }) =>
      (await api.put(`/auth/users/${id}`, data)).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["users"] }); setEditUser(null); },
  });

  const deleteMut = useMutation({
    mutationFn: async (id: string) => (await api.delete(`/auth/users/${id}`)).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["users"] }); setDeleteTarget(null); },
  });

  const roleBadge = (role: string) => {
    const colors: Record<string, string> = {
      admin: "bg-purple-500/20 text-purple-400",
      supervisor: "bg-blue-500/20 text-blue-400",
      agent: "bg-gray-500/20 text-gray-400",
    };
    return (
      <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${colors[role] || colors.agent}`}>
        {role}
      </span>
    );
  };

  const authBadge = (method: string) => {
    const colors: Record<string, string> = {
      ldap: "bg-orange-500/20 text-orange-400",
      voip: "bg-green-500/20 text-green-400",
      local: "bg-gray-500/10 text-gray-500",
    };
    return (
      <span className={`text-[10px] px-1.5 py-0.5 rounded ${colors[method] || colors.local}`}>
        {method}
      </span>
    );
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-[calc(100vh-3.5rem)]">
        <div className="text-text-secondary">Loading users...</div>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Users size={24} className="text-accent" />
          <div>
            <h1 className="text-xl font-bold text-text-primary">User Management</h1>
            <p className="text-sm text-text-secondary">{users.length} users registered</p>
          </div>
        </div>
        {currentUser?.role === "admin" && (
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent/90 transition-colors"
          >
            <Plus size={16} /> Add User
          </button>
        )}
      </div>

      {/* Users Table */}
      <div className="bg-bg-secondary rounded-lg border border-border-subtle overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border-subtle text-xs text-text-secondary">
              <th className="text-left px-4 py-3 font-medium">Name</th>
              <th className="text-left px-4 py-3 font-medium">Email</th>
              <th className="text-left px-4 py-3 font-medium">Role</th>
              <th className="text-left px-4 py-3 font-medium">Extension</th>
              <th className="text-left px-4 py-3 font-medium">Auth</th>
              <th className="text-left px-4 py-3 font-medium">Status</th>
              <th className="text-right px-4 py-3 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {users.map((user) => (
              <tr key={user.id} className="border-b border-border-subtle hover:bg-bg-elevated/50 transition-colors">
                <td className="px-4 py-3 text-sm text-text-primary font-medium">{user.name}</td>
                <td className="px-4 py-3 text-sm text-text-secondary">{user.email}</td>
                <td className="px-4 py-3">{roleBadge(user.role)}</td>
                <td className="px-4 py-3 text-sm text-text-secondary font-mono">{user.extension || "—"}</td>
                <td className="px-4 py-3">{authBadge(user.auth_method)}</td>
                <td className="px-4 py-3">
                  <span className={`text-xs ${user.is_active ? "text-green-400" : "text-red-400"}`}>
                    {user.is_active ? "Active" : "Inactive"}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">
                  <div className="flex items-center justify-end gap-1">
                    <button
                      onClick={() => setEditUser(user)}
                      className="p-1.5 rounded hover:bg-bg-elevated text-text-secondary hover:text-text-primary"
                    >
                      <Pencil size={14} />
                    </button>
                    {currentUser?.role === "admin" && user.email !== currentUser.email && (
                      <button
                        onClick={() => setDeleteTarget(user)}
                        className="p-1.5 rounded hover:bg-red-500/10 text-text-secondary hover:text-red-400"
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {users.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-sm text-text-secondary">
                  No users found. Click "Add User" to create one.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Create Modal */}
      {showCreate && (
        <UserFormModal
          title="Create New User"
          submitLabel="Create"
          onSubmit={(data) => {
            if (!data.email || !data.password) return;
            createMut.mutate({
              email: data.email,
              name: data.name,
              password: data.password,
              role: data.role,
              extension: data.extension,
              department: data.department,
              phone_number: data.phone_number,
            });
          }}
          onClose={() => setShowCreate(false)}
          loading={createMut.isPending}
        />
      )}

      {/* Edit Modal */}
      {editUser && (
        <UserFormModal
          title={`Edit ${editUser.name}`}
          submitLabel="Save"
          initial={editUser}
          onSubmit={(data) => updateMut.mutate({
            id: editUser.id,
            data: {
              name: data.name,
              role: data.role,
              extension: data.extension,
              department: data.department,
              phone_number: data.phone_number,
            },
          })}
          onClose={() => setEditUser(null)}
          loading={updateMut.isPending}
        />
      )}

      {/* Delete Confirm */}
      {deleteTarget && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setDeleteTarget(null)}>
          <div className="bg-bg-secondary border border-border-subtle rounded-xl p-6 w-full max-w-sm" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-semibold text-text-primary mb-2">Delete User</h3>
            <p className="text-sm text-text-secondary mb-6">
              Are you sure you want to deactivate <b className="text-text-primary">{deleteTarget.name}</b>?
            </p>
            <div className="flex justify-end gap-3">
              <button onClick={() => setDeleteTarget(null)} className="px-4 py-2 text-sm rounded-lg border border-border-subtle text-text-secondary hover:text-text-primary">Cancel</button>
              <button
                onClick={() => deleteMut.mutate(deleteTarget.id)}
                className="px-4 py-2 text-sm rounded-lg bg-red-500 text-white hover:bg-red-600"
              >
                {deleteMut.isPending ? "Deleting..." : "Delete"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}


// ─── Reusable Form Modal ────────────────────────────────────

interface UserFormModalProps {
  title: string;
  submitLabel: string;
  initial?: Partial<User>;
  onSubmit: (data: UserFormData) => void;
  onClose: () => void;
  loading?: boolean;
}

interface UserFormData {
  name: string;
  role: UserRole;
  email?: string;
  password?: string;
  extension?: string;
  department?: string;
  phone_number?: string;
}

interface UserFormValues {
  name: string;
  email: string;
  password: string;
  role: UserRole;
  extension: string;
  department: string;
  phone_number: string;
}

function UserFormModal({ title, submitLabel, initial, onSubmit, onClose, loading }: UserFormModalProps) {
  const [form, setForm] = useState<UserFormValues>({
    name: initial?.name || "",
    email: initial?.email || "",
    password: "",
    role: initial?.role || "agent",
    extension: initial?.extension || "",
    department: initial?.department || "",
    phone_number: initial?.phone_number || "",
  });

  const isEdit = !!initial?.id;

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-bg-secondary border border-border-subtle rounded-xl p-6 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-text-primary">{title}</h3>
          <button onClick={onClose} className="p-1 rounded hover:bg-bg-elevated text-text-secondary">
            <X size={18} />
          </button>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            const payload: UserFormData = {
              name: form.name,
              role: form.role,
              extension: form.extension || undefined,
              department: form.department || undefined,
              phone_number: form.phone_number || undefined,
            };
            if (!isEdit) {
              payload.email = form.email;
              payload.password = form.password;
            }
            onSubmit(payload);
          }}
          className="space-y-4"
        >
          {!isEdit && (
            <>
              <div>
                <label className="text-xs text-text-muted mb-1 block">Email</label>
                <input
                  type="email"
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                  required
                  className="w-full bg-bg-elevated border border-border-subtle rounded-lg px-3 py-2 text-sm text-text-primary outline-none focus:border-accent"
                />
              </div>
              <div>
                <label className="text-xs text-text-muted mb-1 block">Password</label>
                <input
                  type="password"
                  value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                  required
                  className="w-full bg-bg-elevated border border-border-subtle rounded-lg px-3 py-2 text-sm text-text-primary outline-none focus:border-accent"
                />
              </div>
            </>
          )}
          <div>
            <label className="text-xs text-text-muted mb-1 block">Name</label>
            <input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              required
              className="w-full bg-bg-elevated border border-border-subtle rounded-lg px-3 py-2 text-sm text-text-primary outline-none focus:border-accent"
            />
          </div>
          <div>
            <label className="text-xs text-text-muted mb-1 block">Role</label>
            <select
              value={form.role}
              onChange={(e) => setForm({ ...form, role: e.target.value as UserRole })}
              className="w-full bg-bg-elevated border border-border-subtle rounded-lg px-3 py-2 text-sm text-text-primary outline-none focus:border-accent"
            >
              <option value="agent">Agent</option>
              <option value="supervisor">Supervisor</option>
              <option value="admin">Admin</option>
            </select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-text-muted mb-1 block">Extension</label>
              <input
                value={form.extension}
                onChange={(e) => setForm({ ...form, extension: e.target.value })}
                placeholder="e.g. 101"
                className="w-full bg-bg-elevated border border-border-subtle rounded-lg px-3 py-2 text-sm text-text-primary outline-none focus:border-accent"
              />
            </div>
            <div>
              <label className="text-xs text-text-muted mb-1 block">Department</label>
              <input
                value={form.department}
                onChange={(e) => setForm({ ...form, department: e.target.value })}
                className="w-full bg-bg-elevated border border-border-subtle rounded-lg px-3 py-2 text-sm text-text-primary outline-none focus:border-accent"
              />
            </div>
          </div>
          <div>
            <label className="text-xs text-text-muted mb-1 block">Phone Number</label>
            <input
              value={form.phone_number}
              onChange={(e) => setForm({ ...form, phone_number: e.target.value })}
              className="w-full bg-bg-elevated border border-border-subtle rounded-lg px-3 py-2 text-sm text-text-primary outline-none focus:border-accent"
            />
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose} className="px-4 py-2 text-sm rounded-lg border border-border-subtle text-text-secondary hover:text-text-primary">
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading}
              className="px-4 py-2 text-sm rounded-lg bg-accent text-white hover:bg-accent/90 disabled:opacity-50"
            >
              {loading ? "Saving..." : submitLabel}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
