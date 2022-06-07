#!/usr/bin/env python3

"""Upload images to kartaview.

The images must have been sequenced before.

Upload images from a directory:

  kv_upload.py ~/Pictures/kartaview/*.jpg
  kv_upload.py -n -v ~/Pictures/kartaview/*.jpg

Upload images from a list of images (one complete filepath per line):

  kv_upload.py @picture_list.txt
  find ~/Pictures/kartaview/ -name "*.jpg" | kv_upload.py @/dev/stdin

"""

import collections
import concurrent.futures
import logging
import operator
from typing import List

from tqdm import tqdm

import kartaview_tools as kt
import kartaview_tools.api10 as api
from kartaview_tools import Geotags


SEQUENCE_THREADS = 2  # upload this many sequences in parallel
IMAGE_THREADS = 2  # upload this many images in parallel per sequence


class SequenceJob:
    """A job that uploads a sequence of images."""

    def __init__(self, args, sequence: List[Geotags]) -> None:
        """Initialize this."""
        self.args = args
        self.sequence = sequence
        self.uploaded = 0
        self.errors = 0

    def upload(self) -> "SequenceJob":  # noqa: C901
        """Upload a sequence of images."""
        self.uploaded = 0
        self.errors = 0

        sequence_id = self.sequence[0].get("sequence_id", 0)

        # create a new sequence on the server
        if sequence_id == 0:
            parameters = {}
            if "deviceName" in self.sequence[0]:
                parameters["deviceName"] = self.sequence[0]["deviceName"]
            sequence_id = api.create_sequence(self.args, parameters)
            logging.info(f"Created sequence {sequence_id}")

            # mark all images as belonging to this open sequence
            for gt in self.sequence:
                gt["sequence_id"] = sequence_id
                gt["sequence_status"] = "open"
                kt.write_sidecar_file(gt["filename"], gt)

        # upload images in sequence
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=IMAGE_THREADS
        ) as executor:
            futures = []
            for gt in sorted(self.sequence, key=operator.itemgetter("sequence_index")):
                if gt.get("status_code", 0) == 200:
                    self.args.pbar.update()
                else:
                    futures.append(
                        executor.submit(api.upload_image, self.args, sequence_id, gt)
                    )
            logging.info(
                f"Uploading {len(futures)} images to sequence {sequence_id} ..."
            )
            for job in concurrent.futures.as_completed(futures):
                try:
                    gt = job.result()
                    kt.write_sidecar_file(gt["filename"], gt)
                    self.uploaded += 1
                    self.args.pbar.update()
                except kt.ImageUploadError as e:
                    self.errors += 1
                    logging.exception(e)

        # cleanup
        if self.errors:
            logging.error(
                f"Uploaded {self.uploaded} images with {self.errors} errors to sequence {sequence_id}"
            )
        if not self.errors or self.args.force_close:
            api.close_sequence(self.args, sequence_id)
            # mark all images as belonging to this closed sequence
            for gt in self.sequence:
                gt["sequence_status"] = "closed"
                kt.write_sidecar_file(gt["filename"], gt)
            logging.info(f"Uploaded {self.uploaded} images to sequence {sequence_id}")
            logging.info(f"Sequence {sequence_id} closed")
        return self


def build_parser():
    """Build the commandline parser."""
    parser = kt.build_parser(__doc__)

    parser.add_argument(
        "images",
        metavar="FILENAME",
        type=str,
        nargs="+",
        help="the images to upload",
    )

    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="dry run: do not upload any images",
    )

    parser.add_argument(
        "--force-close",
        action="store_true",
        help="close open sequence even with pending errors",
    )
    return parser


def main():
    """Run this."""
    args = build_parser().parse_args(namespace=kt.args)
    kt.init_logging(args.verbose)
    kt.read_config_file()

    queued = 0
    errors = 0
    sequences = collections.defaultdict(list)

    for filename_or_glob in args.images:
        for filename in kt.ffmpeg_glob(filename_or_glob):
            logging.debug("Found image: %s" % filename)
            gt = kt.read_sidecar_file(filename)

            if "tmp_sequence_id" not in gt:
                # this image has not been sequenced
                continue

            logging.debug("queued: %s", gt["filename"])
            sequences[gt["tmp_sequence_id"]].append(gt)
            queued += 1

    with tqdm(
        total=queued,
        desc="Dry run" if args.dry_run else "Uploading",
        unit="image",
        smoothing=0,
        # disable=args.verbose == 0,
    ) as args.pbar:

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=SEQUENCE_THREADS
        ) as executor:
            futures = [
                executor.submit(SequenceJob.upload, SequenceJob(args, sequence))
                for sequence in sequences.values()
            ]
            for future in concurrent.futures.as_completed(futures):
                try:
                    job = future.result()
                    errors += job.errors
                except kt.KartaviewError as e:
                    logging.exception(e)

    if errors:
        logging.error(f"There where {errors} errors reported.")
        logging.error("To resume uploading re-run the program with the same arguments.")
    else:
        logging.info("Done.")


if __name__ == "__main__":
    main()
