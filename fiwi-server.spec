# PyInstaller spec for fiwi-server
# Run: .venv/bin/pyinstaller fiwi-server.spec

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

# Collect data/binaries for packages that scatter files at runtime
shazamio_datas, shazamio_bins, shazamio_hidden = collect_all("shazamio")
aiohttp_datas, aiohttp_bins, aiohttp_hidden = collect_all("aiohttp")

a = Analysis(
    ["src/fiwi_filmmusik/__main__.py"],
    pathex=["."],
    binaries=shazamio_bins + aiohttp_bins,
    datas=[
        ("src/fiwi_filmmusik/static", "static"),
        ("assets/ast_model", "ast_model"),
    ] + shazamio_datas + aiohttp_datas,
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
        # async / network
        "asyncio",
        "aiohttp",
        "aiosignal",
        "multidict",
        "yarl",
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
    ] + shazamio_hidden + aiohttp_hidden,
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
