import zipfile
import os
import re
import glob
import logging
import logging.handlers
import argparse
import shutil
import pprint
import random

from datetime import datetime
from pathlib import Path

import mosspy


MOSS_ID = "1234"

LANGUAGE_EXTENSIONS: dict[str, list[str]] = {
    "java": ["java"],
    "cpp": [".cpp", ".h", ".hpp"],
}

file_handler = logging.FileHandler(
    filename=f"mos_moss_{datetime.now().isoformat()}.log", mode="w"
)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(threadName)s] [%(levelname)s] %(message)s",
    handlers=[file_handler, logging.StreamHandler()],
)
log = logging.getLogger()


def cleanup_files(path):
    """Currently just removes __MACOSX folders."""
    macos_folders = glob.glob(f"{path}/**/__MACOSX", recursive=True)
    for f in macos_folders:
        shutil.rmtree(f)


def flatten_folder(destination):
    """Flatten folders containing a single folder."""
    content = os.listdir(destination)
    if len(content) == 1:
        folder = os.path.join(destination, content[0])

        log.debug(f"Flattening {folder}")
        for f in os.listdir(folder):
            shutil.move(os.path.join(folder, f), destination)
        os.rmdir(folder)


def unzip_canvas_submission(canvas_zip, zip_output, original_name=False) -> None:
    """
    Unzip the Canvas submission folder and place them in a folder.
    Set `original_name` to `True` to keep student's ZIP file original name.
    This doesn't work consistently, notably with resubmissions.
    """
    with zipfile.ZipFile(canvas_zip, "r") as zf:
        for submission in zf.infolist():
            # [last][first]_[int]_[int]_[original_filename]
            res = re.match(r"(\w+_\w*_\d+\d+)_(.+)\.", submission.filename)
            try:
                folder_name = res[2] if original_name else res[1]
            except TypeError:
                folder_name = submission.filename

            # log.debug(f"Extracting {folder_name}")

            b = zf.open(submission, "r")
            with zipfile.ZipFile(b) as student_zip:
                path = os.path.join(zip_output, folder_name)
                student_zip.extractall(path=path)
                cleanup_files(path)
                flatten_folder(path)


def list_files(folder: str, language="") -> list[str]:
    """
    List files from the provided folder. If `language` is provided, the
    resulting list will only contain files that match the extension of the
    language.
    """
    files = []
    for ext in LANGUAGE_EXTENSIONS.get(language.lower(), ""):
        files += glob.glob(f"{folder}/**/*{ext}", recursive=True)

    new_files = []
    for f in files:
        if (
            os.path.isfile(f)
            and not f.endswith("pdf")
            and not f.endswith("jar")
            and os.path.getsize(f) > 0,
        ):
            new_files.append(f)
    return new_files


def create_moss_comments(**kwargs) -> str:
    msg = []
    if v := kwargs.get("base_files"):
        msg.append(f"<b>Base files:</b> {v}")
    if v := kwargs.get("solutions"):
        msg.append(f"<b>Solutions:</b> {v}")

    if v := kwargs.get("max_submissions"):
        msg.append(f"<b>Max submissions:</b> {v}")
        if v := kwargs.get("submission_folders"):
            msg.append(f"<b>Submissions in this batch:</b><br>{'<br>'.join(v)}")
    return "<br><br>".join(msg)


def stage_moss_files(
    zip_output: str,
    language: str = "",
    max_submissions=0,
    base_files=None,
    solutions=None,
) -> mosspy.Moss:
    moss = mosspy.Moss(user_id=None, language=language)

    files = []
    submission_folders = []

    if max_submissions:
        folders = glob.glob(f"{zip_output}/*", recursive=True)
        random.shuffle(folders)
        submission_folders = folders[:max_submissions]
    else:
        submission_folders = [zip_output]

    for folder in submission_folders:
        files += list_files(folder, language)

    for f in files:
        moss.addFile(f)

    if base_files:
        files = list_files(base_files, language)
        if not files:
            raise FileNotFoundError(f"{base_files} returned no matches for base files")
        for f in files:
            moss.addBaseFile(f)

    if solutions:
        files = list_files(solutions, language)
        if not files:
            raise FileNotFoundError(
                f"{solutions} returned no matches for online solutions"
            )
        for f in files:
            moss.addFile(f)

    moss.setCommentString(
        create_moss_comments(
            max_submissions=max_submissions,
            submission_folders=submission_folders,
            base_files=base_files,
            solutions=solutions,
        )
    )

    moss.setDirectoryMode(1)
    return moss


def send_to_moss(
    moss: mosspy.Moss, report_path: str, user_id=None, no_report=False, count=1
):
    moss.user_id = user_id or os.getenv("user_id") or MOSS_ID

    if not moss.user_id:
        raise ValueError("No MOSS ID found")

    log.debug(f"Sending to MOSS with: {pprint.pformat(moss.__dict__)}")
    url = moss.send(lambda file_path, _: log.debug(f"Uploading: {file_path}"))
    log.info("Report URL: " + url)

    log.info("Saving report page")
    Path(report_path).mkdir(parents=True, exist_ok=True)
    moss.saveWebPage(url, f"{report_path}/report{count}.html")

    if no_report:
        return

    log.info("Downloading report")
    Path(f"{report_path}/report{count}").mkdir(parents=True, exist_ok=True)
    mosspy.download_report(
        url, f"{report_path}/report{count}", connections=8, log_level=log.level
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Utility for unzipping Canvas submission and uploading files to MOSS."
    )

    parser.add_argument("zip_file", help="The submission ZIP file from Canvas.")
    parser.add_argument("language", help="Programming language for the assignment.")

    parser.add_argument(
        "--no-report",
        help="Do not save MOSS report to local machine.",
        action="store_true",
    )
    parser.add_argument(
        "--original-name",
        help="""
        Keep the submission's original name when unzipping.
        Note that this doesn't work consistently, notably with resubmissions.
        """,
        action="store_true",
    )
    parser.add_argument(
        "--verbose",
        help="Log everything.",
        action="store_true",
    )

    parser.add_argument(
        "-n",
        "--max-submissions",
        metavar="n",
        help="Maximum number of submissions per batch.",
        type=int,
        default=0,
    )
    parser.add_argument(
        "-r",
        "--repeat",
        metavar="n",
        help="Number of times to perform repeated submissions.",
        type=int,
        default=1,
    )
    parser.add_argument(
        "-o",
        "--zip-output",
        metavar="path",
        help="Path to extract the submission ZIP file into.",
        default="./zip_output",
    )
    parser.add_argument(
        "-ro",
        "--report-output",
        metavar="path",
        help="Path to save MOSS report(s).",
        default="./report",
    )
    parser.add_argument(
        "-b",
        "--base-files",
        metavar="path",
        help="""
        Path to base files provided by the instructor, such as starter code.
        Helps MOSS filter boilerplates that are common throughout submissions.
        """,
    )
    parser.add_argument(
        "-s",
        "--solutions",
        metavar="path",
        help="""
        Path to online solutions to check against.
        This will be sent to MOSS alongside student's submissions.
        Bypasses maximum number of submissions, if supplied.
        """,
    )

    return parser.parse_args()


if __name__ == "__main__":
    opt = parse_args()

    log.setLevel(logging.DEBUG if opt.verbose else logging.INFO)
    log.debug(f"CLI options: {pprint.pformat(opt.__dict__)}")

    unzip_canvas_submission(
        canvas_zip=opt.zip_file,
        zip_output=opt.zip_output,
        original_name=opt.original_name,
    )

    for n in range(1, opt.repeat + 1):
        log.info(f"Sending batch {n}/{opt.repeat} to MOSS")
        moss = stage_moss_files(
            zip_output=opt.zip_output,
            language=opt.language,
            max_submissions=opt.max_submissions,
            base_files=opt.base_files,
            solutions=opt.solutions,
        )
        send_to_moss(
            moss, no_report=opt.no_report, report_path=opt.report_output, count=n
        )
