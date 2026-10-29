-- Source-version checkpoints are committed with derived records, including valid empty results.
CREATE TABLE IF NOT EXISTS public.processing_checkpoints (
    stage text NOT NULL CHECK (stage IN ('extract', 'entities')),
    conversation_id bigint NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
    fingerprint text NOT NULL,
    model text NOT NULL,
    elapsed_seconds double precision NOT NULL CHECK (elapsed_seconds >= 0),
    output_count integer NOT NULL CHECK (output_count >= 0),
    completed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (stage, conversation_id)
);
