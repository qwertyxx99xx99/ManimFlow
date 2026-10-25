import pathlib
import shutil

ROOT = pathlib.Path(__file__).resolve().parent
EXA_PROVIDER = "exa-enhanced"
EXA_MODEL = "google/gemini-2.5-flash"
BAML_VERSION = "0.223.0"


def modules_dir(pi_command):
    return pathlib.Path(pi_command).parent.parent


def exa_ready(pi_command):
    return (modules_dir(pi_command) / "@boundaryml" / "baml" / "package.json").is_file()


def install_exa_extension(agent_dir, pi_command):
    if not exa_ready(pi_command):
        raise RuntimeError("The Exa provider needs @boundaryml/baml next to the Pi installation.")
    extensions = agent_dir / "extensions"
    extensions.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "pi_extensions" / "exa_enhanced.ts", extensions / "exa_enhanced.ts")
    shutil.copytree(
        ROOT / "pi_baml" / "baml_client",
        agent_dir / "baml_exa" / "baml_client",
        dirs_exist_ok=True,
    )
    link = agent_dir / "node_modules"
    if not link.exists():
        link.symlink_to(modules_dir(pi_command), target_is_directory=True)
