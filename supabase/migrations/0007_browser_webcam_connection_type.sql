-- 0007: allow browser-captured webcams as a camera connection type.
--
-- A `browser_webcam` camera is opened by the visitor's browser with
-- getUserMedia; the AI server only records its status and receives frames.
-- The original CHECK constraint (0001) only allowed the four server-side
-- types, so it has to be widened. No data changes; existing rows are unaffected.

alter table public.cameras drop constraint if exists cameras_connection_type_check;

alter table public.cameras
    add constraint cameras_connection_type_check
    check (connection_type in ('local_webcam', 'browser_webcam', 'ip_camera', 'rtsp', 'http_mjpeg'));
