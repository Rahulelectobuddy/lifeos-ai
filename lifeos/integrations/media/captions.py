def timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


class SceneSubtitles:
    """Scene-timed captions; not a forced-alignment or speech-recognition provider."""

    def generate(self, manifest):
        rows, elapsed, index = [], 0.0, 1
        for scene in manifest["scenes"]:
            words = scene["narration_hindi"].split()
            chunks = [" ".join(words[start : start + 8]) for start in range(0, len(words), 8)]
            step = scene["duration_seconds"] / len(chunks)
            for offset, text in enumerate(chunks):
                clean = text.replace("\r", " ").replace("\n", " ")
                rows.append(
                    f"{index}\n{timestamp(elapsed + offset * step)} --> {timestamp(elapsed + (offset + 1) * step)}\n{clean}\n"
                )
                index += 1
            elapsed += scene["duration_seconds"]
        return "\n".join(rows)
