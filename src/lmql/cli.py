import sys
import subprocess
import os
import argparse
import lmql
from lmql.runtime.interpreter import LMQLResult

import lmql.version as version_info

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def cmd_serve_model():
    """emoji:🏄 Serve a 🤗 Transformers model via the LMQL inference API"""
    from lmql.models.lmtp.lmtp_serve import cli
    os.chdir(project_root)
    cli(sys.argv[2:])

def cmd_chat():
    """emoji:💬 Serve a given query file as an interactive LMQL chatbot"""
    if len(sys.argv) == 2:
        print("Usage: 💬 lmql chat <file>")
        return
    file = sys.argv[2]
    absolute_path = os.path.abspath(file)
    subprocess.run([sys.executable, "-m", "lmql.lib.chat", absolute_path], cwd=project_root)

def cmd_run():
    """
    emoji:🏃 run a LMQL script (e.g. "lmql run latest/hello.lmql")
    """
    import asyncio
    import time

    start = time.time()

    parser = argparse.ArgumentParser(description="Runs a LMQL program.")
    parser.add_argument("lmql_file", type=str, help="path to the LMQL file to run")
    parser.add_argument("-q", "--quiet", action="store_true", dest="quiet", help="don't print anything")
    parser.add_argument("--time", action="store_true", dest="time", help="Time the query.")
    parser.add_argument("--certificate", type=str, default="False", help="Create a inference certificate for the executed query (True to print, path to save on disk)")

    parser.add_argument("--no-clear", action="store_true", dest="no_clear", help="don't clear inbetween printing results (deprectated, use --quiet instead)")
    parser.add_argument("--no-realtime", action="store_true", dest="no_realtime", help="don't print text as it's being generated (deprectated, use --quiet instead)")

    args = parser.parse_args(sys.argv[2:])

    absolute_path = os.path.abspath(args.lmql_file)

    if args.quiet:
        args.no_clear = True
        args.no_realtime = True

    writer = lmql.printing
    writer.clear = not args.no_clear
    writer.print_output = not args.no_realtime

    # parse 'certificate'
    certificate = False
    if args.certificate.lower() == "true":
        certificate = True
    elif args.certificate.lower() != "false":
        certificate = args.certificate

    kwargs = {
        "output_writer": writer,
        "certificate": certificate,
        "__name__": f"<lmql run '{args.lmql_file}'>",
    }

    if os.path.exists(absolute_path):
        results = asyncio.run(lmql.run_file(absolute_path, **kwargs))
    else:
        code = args.lmql_file
        results = asyncio.run(lmql.run(code, **kwargs))

    if type(results) is not list:
        results = [results]

    for r in results:
        if isinstance(r, LMQLResult):
            for v in [v for v in r.variables if v.startswith("P(")]:
                distribution = r.variables[v]
                max_prob = max(p for _,p in distribution)
                labels = []
                for value, prob in distribution:
                    label = value if prob != max_prob else f"{value} (*)"
                    labels.append(label)
                max_length = max(len(str(l)) for l in labels)

                print(v)
                for (value, prob), label in zip(distribution, labels):
                    label = label.ljust(max_length)
                    print(f" - {label} {prob}")

    if args.time:
        print("Query took:", time.time() - start, "seconds")

def ensure_node_install():
    try:
        subprocess.check_output(["node", "--version"], stderr=subprocess.DEVNULL).decode("utf-8").strip()
    except:
        print("""node.js is not installed. Please install it to use the LMQL playground.

If your Python installation is managed by conda, you can install node.js with:

    conda install nodejs=14.20 -c conda-forge
    
Alternatively, you can find instructions for installing node.js on your system on the official website: https://nodejs.org/en/download/.""")
        sys.exit(1)

def cmd_playground():
    """
    emoji:💻 runs LMQL in development mode (hot-reloading python and debugger implementation)
    """
    ensure_node_install()
    
    parser = argparse.ArgumentParser(description="Launches an instance of the LMQL playground.")
    parser.add_argument("--live-port", type=int, default=3004, help="port to use to host the LMQL live server")
    parser.add_argument("--ui-port", type=int, default=3000, help="port to use to host the LMQL debugger UI")
    
    args = parser.parse_args(sys.argv[2:])

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print(f"[lmql playground {project_root}, liveserver=localhost:{args.live_port}, ui=localhost:{args.ui_port}]")

    # # make sure yarn is installed
    if subprocess.call(["yarn", "--version"]) != 0:
        subprocess.run(['npm', 'install', '-g', 'yarn'], check=True)

    # repo commit
    if os.path.exists(os.path.join(project_root, "../.git")):
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project_root).decode("utf-8").strip()
        commit = commit[:7]
        has_uncomitted_files = len(subprocess.check_output(["git", "status", "--porcelain"], cwd=project_root).decode("utf-8").strip()) > 0
        if has_uncomitted_files:
            commit += ' (dirty)'
            commit = f'"{commit}"'
    else:        
        commit = version_info.commit

    # Ensure that we can download dependencies before we start either live.js or the debug server
    yarn_cwd_live = os.path.join(project_root, "lmql/ui/live")
    subprocess.run(['yarn'], cwd=yarn_cwd_live, check=True)

    yarn_cwd_playground = os.path.join(project_root, 'lmql/ui/playground')
    subprocess.run(['yarn'], cwd=yarn_cwd_playground, check=True)

    # live server that executes LMQL queries and returns results and debugger data
    live_process = subprocess.Popen(['yarn', 'cross-env', 'node', 'live.js'],
        cwd=yarn_cwd_live,
        env=dict(os.environ, PORT=str(args.live_port)),
    )

    # UI that displays the debugger (uses live server API for data and remote execution)
    ui_modern_process = subprocess.Popen(
        ['yarn', 'cross-env', 'yarn', 'run', 'start'],
        cwd=yarn_cwd_playground,
        env=dict(os.environ, REACT_APP_BUILD_COMMIT=str(commit), REACT_APP_SOCKET_PORT=str(args.live_port)),
    )

    try:
        live_process.wait()
        ui_modern_process.wait()
    except KeyboardInterrupt:
        print("[lmql playground] Ctrl+C pressed, exiting...")
        live_process.terminate()
        ui_modern_process.terminate()

def cmd_usage():
    """
    emoji:❓ show usage information
    """
    commands = [f for f in globals().values() if callable(f) and f.__name__.startswith("cmd_")]
    commands = [(f.__name__[4:], f.__doc__ or "<no description") for f in commands]

    def format_command_line(c, doc):
        emoji = "   "
        doc = doc.strip()
        if doc.startswith("emoji:"):
            emoji, doc = doc[len("emoji:"):].split(" ", 1)
            emoji += " "
            doc = doc.strip()
        return ("\n  " if c == "usage" else "") + emoji + c.replace("_", "-") + " " * (20 - len(c)) + " " + doc

    command_list = "\n".join(
        [f"  {format_command_line(name, doc)}" for name, doc in commands]
    )
    if version_info.commit == "dev":
        print(f"[LMQL Dev Version {project_root}]\n")
    print(f"""USAGE: lmql <command>

Commands:

{command_list}
""")


def cmd_version():
    """
    emoji:📦 show version information
    """
    print(f"lmql v{version_info.version}")
    if version_info.commit != "dev":
        print(f"  commit: {version_info.commit}")
    print(f"  path: {project_root}/lmql")
    if version_info.build_on != "dev":
        print(f"  build on: {version_info.build_on}")

def hello():
    import asyncio
    backend = None
    # check for additional arg
    if len(sys.argv) > 2:
        backend = sys.argv[2]
        if backend not in ["hf", "openai"]:
            print(f'Invalid backend, please specify one of {", ".join(["hf", "openai"])}')
            sys.exit(1)

    if backend is None or backend == "hf":
        code_local = """
    argmax "Hello[WHO]" from "local:gpt2-medium" where len(TOKENS(WHO)) < 10
    """
        print("[Greeting 🤗 Transformers]")
        asyncio.run(lmql.run(code_local, output_writer=lmql.printing))

    if backend is None or backend == "openai":
        print("[Greeting OpenAI]")
        code_openai = 'argmax "Hello[WHO]" from "openai/text-ada-001" where len(TOKENS(WHO)) < 10 and not "\\n" in WHO'
        asyncio.run(lmql.run(code_openai, output_writer=lmql.printing, model="openai/text-ada-001"))

def basic_samples():
    from lmql.tests.optional.openai.test_sample_queries import main
    import asyncio
    asyncio.run(main())

hidden_commands = {
    "hello": hello,
    "test": basic_samples
}

def main():
    if len(sys.argv) < 2:
        cmd_usage()
        sys.exit(1)
    # get all functions defined in this file
    functions = [f for f in globals().values() if callable(f)]
    # get all functions that start with "cmd_"
    commands = [f for f in functions if f.__name__.startswith("cmd_")]
    # get the command function
    command_name = sys.argv[1]
    if command_name == "dev":
        print("'lmql dev' is deprecated, use 'lmql playground' instead.")
        command_name = "playground"
    if command_name in hidden_commands.keys():
        hidden_commands[command_name]()
        sys.exit(0)
    command = [
        f
        for f in commands
        if f.__name__
        in [f"cmd_{command_name}", "cmd_" + command_name.replace("-", "_")]
    ]
    if not command:
        print(f"Unknown command: {sys.argv[1]}")
        cmd_usage()
        sys.exit(1)
    command[0]()

if __name__ == "__main__":
    main()
