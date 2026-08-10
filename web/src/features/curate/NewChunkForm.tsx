import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, X } from "lucide-react";

import { curateApi } from "@/lib/api";
import { useToast } from "@/components/Toaster";

/**
 * Record a memory chunk by hand.
 *
 * Most memory arrives through extraction. This exists because some facts —
 * a contact, a decision taken in a meeting — are worth remembering the moment
 * you learn them, and the alternative was dropping to SQL. The old GUI had
 * this form; it is here for parity.
 */
export function NewChunkForm() {
  const [open, setOpen] = useState(false);
  const [content, setContent] = useState("");
  const [category, setCategory] = useState("insight");
  const [project, setProject] = useState("");
  const [tags, setTags] = useState("");
  const toast = useToast();
  const qc = useQueryClient();

  const { data: cats } = useQuery({
    queryKey: ["curate", "categories"],
    queryFn: curateApi.categories,
    enabled: open,
  });

  const create = useMutation({
    mutationFn: () =>
      curateApi.createChunk({
        content: content.trim(),
        category,
        project_name: project.trim() || null,
        tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
      }),
    onSuccess: (res) => {
      setContent("");
      setTags("");
      setOpen(false);
      qc.invalidateQueries({ queryKey: ["curate"] });
      toast.push({
        message: res.message,
        onUndo: res.undo_token
          ? async () => {
              await curateApi.undo(res.undo_token!);
              qc.invalidateQueries({ queryKey: ["curate"] });
            }
          : undefined,
      });
    },
    onError: (e) =>
      toast.push({ message: (e as Error).message, tone: "error", duration: 8000 }),
  });

  if (!open) {
    return (
      <button type="button" className="button" onClick={() => setOpen(true)}>
        <Plus size={13} aria-hidden />
        New chunk
      </button>
    );
  }

  return (
    <form
      className="newchunk"
      onSubmit={(e) => {
        e.preventDefault();
        if (content.trim()) create.mutate();
      }}
    >
      <div className="newchunk-head">
        <strong>New memory chunk</strong>
        <button type="button" className="icon-button" onClick={() => setOpen(false)} aria-label="Cancel">
          <X size={14} aria-hidden />
        </button>
      </div>

      <label className="field">
        <span>Content</span>
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          rows={4}
          required
          autoFocus
          placeholder="The fact you want remembered."
        />
      </label>

      <div className="newchunk-row">
        <label className="field">
          <span>Category</span>
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            {(cats?.categories ?? [category]).map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Project</span>
          <input value={project} onChange={(e) => setProject(e.target.value)} placeholder="optional" />
        </label>
        <label className="field">
          <span>Tags</span>
          <input
            value={tags}
            onChange={(e) => setTags(e.target.value)}
            placeholder="comma, separated"
          />
        </label>
      </div>

      <button type="submit" className="button" disabled={!content.trim() || create.isPending}>
        {create.isPending ? "Saving…" : "Save"}
      </button>
    </form>
  );
}
