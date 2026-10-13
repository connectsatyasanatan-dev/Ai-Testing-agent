/**
 * CreateProjectModal.tsx — Modal for creating a new workspace project.
 */
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/Button";
import { ErrorMessage } from "@/components/ui/ErrorMessage";
import { createProject } from "@/lib/api-client";
import styles from "./CreateProjectModal.module.css";

interface CreateProjectModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function CreateProjectModal({ isOpen, onClose }: CreateProjectModalProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const createMutation = useMutation({
    mutationFn: createProject,
    onSuccess: (newProj) => {
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      onClose();
      setName("");
      setDescription("");
      setErrorMsg(null);
      navigate(`/projects/${newProj.id}`);
    },
    onError: (err: Error) => {
      setErrorMsg(err.message || "Failed to create project.");
    },
  });

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setErrorMsg(null);
    createMutation.mutate({
      name: name.trim(),
      description: description.trim() || undefined,
    });
  };

  return (
    <div className={styles.backdrop} onClick={onClose} role="dialog" aria-modal="true">
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <div className={styles.iconCircle}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
            </svg>
          </div>
          <div>
            <h2 className={styles.title}>Create New Project</h2>
            <p className={styles.subtitle}>
              Set up a dedicated workspace for test plans, environments, and runs.
            </p>
          </div>
        </div>

        {errorMsg && <ErrorMessage message={errorMsg} />}

        <form onSubmit={handleSubmit} className={styles.form}>
          <div className={styles.formGroup}>
            <label htmlFor="project-name" className={styles.label}>
              Project Name <span style={{ color: "var(--color-danger)" }}>*</span>
            </label>
            <input
              id="project-name"
              type="text"
              autoFocus
              required
              placeholder="e.g. Core Payment Gateway"
              className={styles.input}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>

          <div className={styles.formGroup}>
            <label htmlFor="project-description" className={styles.label}>
              Description (Optional)
            </label>
            <textarea
              id="project-description"
              rows={3}
              placeholder="Describe the scope or targets for this test project..."
              className={styles.textarea}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          <div className={styles.actions}>
            <Button variant="secondary" onClick={onClose} type="button">
              Cancel
            </Button>
            <Button
              variant="primary"
              type="submit"
              disabled={createMutation.isPending || !name.trim()}
            >
              {createMutation.isPending ? "Creating..." : "Create Project"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
