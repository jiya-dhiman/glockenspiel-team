import sys, json
from music21 import converter, tempo

score = converter.parse(sys.argv[1]).stripTies()
part = score.parts[0]                      # first instrument (the melody)

# Tempo from the file if it has one, otherwise 120 BPM
marks = part.flatten().getElementsByClass(tempo.MetronomeMark)
bpm = marks[0].number if marks and marks[0].number else 120

notes = []
for n in part.flatten().notesAndRests:
    beats = float(n.quarterLength)
    if n.isRest:
        notes.append({"note": "rest", "midi": None, "beats": beats})
    else:
        p = max(n.pitches, key=lambda x: x.midi)   # top note if it's a chord
        notes.append({"note": p.nameWithOctave, "midi": p.midi, "beats": beats})

for item in notes:
    item["seconds"] = round(item["beats"] * 60 / bpm, 3)
    print(item["note"], item["beats"], item["seconds"])

with open("notes.json", "w") as f:
    json.dump({"bpm": bpm, "notes": notes}, f, indent=2)