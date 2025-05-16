from faster_whisper import WhisperModel

model = WhisperModel("medium", compute_type="float16")  # or "int8" for CPU

segments, info = model.transcribe("output.mp4", beam_size=5)

with open("output.srt", "w", encoding="utf-8") as f:
    for i, segment in enumerate(segments, 1):
        start = segment.start
        end = segment.end
        text = segment.text.strip()

        # Format time
        def format_time(seconds):
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = seconds % 60
            return f"{h:02}:{m:02}:{s:06.3f}".replace('.', ',')

        f.write(f"{i}\n{format_time(start)} --> {format_time(end)}\n{text}\n\n")
