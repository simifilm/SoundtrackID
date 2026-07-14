# PyInstaller spec for fiwi-server
# Run: .venv/bin/pyinstaller fiwi-server.spec

import glob
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

# acrcloud's native fingerprint extractor is imported by the *bare* name
# `import acrcloud_extr_tool` (acrcloud/__init__.py appends its own dir to
# sys.path). collect_all() does not pick up the compiled extension, so bundle it
# explicitly INTO the acrcloud/ package dir, where that sys.path trick finds it.
try:
    import acrcloud as _acrcloud_pkg

    _acr_dir = os.path.dirname(_acrcloud_pkg.__file__)
    _acr_extr_bins = [
        (p, "acrcloud")
        for p in glob.glob(os.path.join(_acr_dir, "acrcloud_extr_tool.*"))
        if not p.endswith(".py")
    ]
except ImportError:
    _acr_extr_bins = []

# Collect data/binaries for packages that scatter files at runtime
shazamio_datas, shazamio_bins, shazamio_hidden = collect_all("shazamio")
aiohttp_datas, aiohttp_bins, aiohttp_hidden = collect_all("aiohttp")
mutagen_datas, mutagen_bins, mutagen_hidden = collect_all("mutagen")
# acrcloud ships a native fingerprint extractor (acrcloud_extr_tool.so) that
# PyInstaller won't pick up on its own — collect_all grabs the .so too.
acrcloud_datas, acrcloud_bins, acrcloud_hidden = collect_all("acrcloud")

# Bundle .env if present so the .app has TMDb credentials
_env_extra = [(".env", ".")] if os.path.exists(".env") else []

a = Analysis(
    ["src/fiwi_filmmusik/__main__.py"],
    pathex=["."],
    binaries=shazamio_bins + aiohttp_bins + mutagen_bins + acrcloud_bins + _acr_extr_bins,
    datas=[
        ("src/fiwi_filmmusik/static", "static"),
        ("assets/ast_model", "ast_model"),
    ] + shazamio_datas + aiohttp_datas + mutagen_datas + acrcloud_datas + _env_extra,
    hiddenimports=[
        # fiwi_filmmusik modules
        "fiwi_filmmusik",
        "fiwi_filmmusik.app",
        "fiwi_filmmusik.pipeline",
        "fiwi_filmmusik.classifiers",
        "fiwi_filmmusik.detection",
        "fiwi_filmmusik.aggregator",
        "fiwi_filmmusik.chunker",
        "fiwi_filmmusik.loaders",
        "fiwi_filmmusik.isolators",
        "fiwi_filmmusik.models",
        "fiwi_filmmusik.config",
        # metadata enrichment
        "fiwi_filmmusik.metadata",
        "fiwi_filmmusik.metadata.enricher",
        "fiwi_filmmusik.metadata.film",
        "fiwi_filmmusik.metadata.music",
        "fiwi_filmmusik.metadata.mp4_tags",
        "httpx",
        "mutagen",
        "dotenv",
        # acrcloud detection provider (+ native extractor module)
        "acrcloud",
        "acrcloud.recognizer",
        "acrcloud.acrcloud_extr_tool",
        # uvicorn
        "uvicorn",
        "uvicorn.main",
        "uvicorn.config",
        "uvicorn.lifespan.on",
        "uvicorn.logging",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        # fastapi / starlette
        "fastapi",
        "starlette",
        "starlette.routing",
        "starlette.staticfiles",
        "starlette.responses",
        "anyio",
        "anyio._backends._asyncio",
        "h11",
        "python_multipart",
        # async / network / ssl
        "asyncio",
        "aiohttp",
        "aiosignal",
        "multidict",
        "yarl",
        "truststore",
        # audio
        "librosa",
        "scipy",
        "numpy",
        "av",
        # onnx
        "onnxruntime",
        # misc
        "yaml",
        "email.mime.text",
        "email.mime.multipart",
    ] + shazamio_hidden + aiohttp_hidden + mutagen_hidden + acrcloud_hidden,
    excludes=[
        "torch",
        "torchvision",
        "torchaudio",
        "transformers",
        "demucs",
        "tensorflow",
        "jax",
        "flax",
        "IPython",
        "matplotlib",
        "tkinter",
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="fiwi-server",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="fiwi-server",
)
