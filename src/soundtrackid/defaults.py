"""What an analysis uses unless told otherwise.

The server's form, the CLI's options and the pipeline default to these, and the
app's page (index.html) starts from the same values; tests keep them alike.
Imports nothing, so the app can read them without loading the pipeline.
"""

DEFAULT_API = "shazam"  # the provider(s), comma-separated to let them vote
MUSIC_THRESHOLD = 0.2  # a window is music when the classifier scores it above
CLASSIFICATION_HOP_S = 5.0  # seconds between overlapping classified windows
BRIDGE_GAP_S = 0.0  # seconds without music that join clips of the same track
MAX_SEGMENT_S = 30.0  # longest piece of a cue sent for identification
