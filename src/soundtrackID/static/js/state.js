// The page's elements and the state its scripts share. Loaded first.

const dropZone        = document.getElementById('drop-zone');
const fileInput       = document.getElementById('file-input');
const settingsPanel   = document.getElementById('settings-panel');
const progressView    = document.getElementById('progress-view');
const resultsView     = document.getElementById('results-view');
const waveformContainer = document.getElementById('waveform-container');
const waveformCanvas  = document.getElementById('waveform-canvas');
const waveformScroll  = document.getElementById('waveform-scroll');
const minimapCanvas   = document.getElementById('minimap-canvas');
const videoWrap       = document.getElementById('video-preview-wrap');
const videoEl         = document.getElementById('video-preview');
const overviewWrap    = document.getElementById('overview-wrap');
const filmstripWrap   = document.getElementById('filmstrip-wrap');
const filmstripCanvas = document.getElementById('filmstrip-canvas');
const overviewPlayhead = document.getElementById('overview-playhead');
const wfPlayhead      = document.getElementById('wf-playhead');
const videoToggle     = document.getElementById('video-toggle');
let filmstripSprite   = null;   // loaded sprite Image
let filmstripMeta     = null;   // {count, thumb_width, thumb_height}
let hasVideo          = false;
let activeSegmentEnd  = null;   // when playing a cue, pause the video at this time


const wfMarker        = document.getElementById('wf-marker');
const zoomControls    = document.getElementById('wf-zoom-controls');
const zoomInBtn       = document.getElementById('wf-zoom-in');
const zoomOutBtn      = document.getElementById('wf-zoom-out');
const zoomResetBtn    = document.getElementById('wf-zoom-reset');
const zoomLabel       = document.getElementById('wf-zoom-label');
const classFill       = document.getElementById('class-fill');
const identFill       = document.getElementById('ident-fill');
const progBars        = document.getElementById('prog-bars');
const stallNotice     = document.getElementById('stall-notice');
let identFarthestEnd  = 0;   // farthest processed window end (identification frontier)
let identReaped       = 0;   // identification windows processed so far
let identTotal        = 0;   // total identification windows (known once classification finishes)
let lastEventAt       = 0;   // timestamp of the last SSE event (stall detection)
let stallTimer        = null;
const errorArea       = document.getElementById('error-area');
const resultsBody     = document.getElementById('results-body');
const resultsTable    = document.getElementById('results-table');
const emptyState      = document.getElementById('empty-state');
const resetBtn        = document.getElementById('reset-btn');
const cancelBtn       = document.getElementById('cancel-btn');
const downloadBtn     = document.getElementById('download-btn');

let currentRunId  = null;
let activeReader  = null;
let currentResults = null;
let currentVideoName = null;
let pendingStagingId = null;   // staged upload awaiting confirmation → analysis
let pendingFilm = {};          // full film metadata read/looked-up in the confirm step
let resumeRunning = false;     // its results replace the table: no edits meanwhile
