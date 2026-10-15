-- Smart Agri Surveillance
-- Migration 0006: Private video storage for Modal processing

-- 1. Add a storage path to each video analysis job.
ALTER TABLE public.video_analysis_jobs
ADD COLUMN IF NOT EXISTS video_path text;

-- 2. Create a private bucket for uploaded videos.
INSERT INTO storage.buckets (
    id,
    name,
    public,
    file_size_limit,
    allowed_mime_types
)
VALUES (
    'videos',
    'videos',
    false,
    2147483648,
    ARRAY[
        'video/mp4',
        'video/x-msvideo',
        'video/quicktime',
        'video/x-matroska',
        'video/webm',
        'video/x-m4v'
    ]
)
ON CONFLICT (id) DO UPDATE
SET
    public = false,
    file_size_limit = 2147483648,
    allowed_mime_types = EXCLUDED.allowed_mime_types;

-- 3. Users can read only their own video objects.
DROP POLICY IF EXISTS videos_read_own ON storage.objects;

CREATE POLICY videos_read_own
ON storage.objects
FOR SELECT
TO authenticated
USING (
    bucket_id = 'videos'
    AND (storage.foldername(name))[1] = auth.uid()::text
);

-- 4. Users can upload only into their own folder.
DROP POLICY IF EXISTS videos_insert_own ON storage.objects;

CREATE POLICY videos_insert_own
ON storage.objects
FOR INSERT
TO authenticated
WITH CHECK (
    bucket_id = 'videos'
    AND (storage.foldername(name))[1] = auth.uid()::text
);

-- 5. Users can delete only their own video objects.
DROP POLICY IF EXISTS videos_delete_own ON storage.objects;

CREATE POLICY videos_delete_own
ON storage.objects
FOR DELETE
TO authenticated
USING (
    bucket_id = 'videos'
    AND (storage.foldername(name))[1] = auth.uid()::text
);