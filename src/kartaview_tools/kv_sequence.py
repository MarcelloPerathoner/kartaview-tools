#!/usr/bin/env python3

"""
Extract EXIF info from discrete image files and sequence them.

Extract Latitude, Longitude, Altitude, DateTime and Heading from the image files
and put the information into sidecar files (same filename with an added extension
of .kv).  Then sort the images into sequences.

Process images from a directory:

  kv_sequence.py ~/Pictures/kartaview/*.jpg

or from many directories:

  kv_sequence.py ~/Pictures/0001/*.jpg ~/Pictures/0002/*.jpg

Process images from a list of images (one complete filepath per line):

  kv_sequence.py @picture_list.txt
  find ~/Pictures/kartaview -name "*.jpg" | kv_sequence.py @/dev/stdin

Process images extracted from a backfacing cam:

  kv_sequence.py --camera-yaw=180 ~/Pictures/0001/*.jpg
"""


import logging
import re
import uuid

import piexif

import kartaview_tools as kt
from kartaview_tools import gpsfix

DEFAULT_MAX_TIME = 5 * 60.0  # in seconds. max time between images in same sequence
DEFAULT_MAX_DISTANCE = 100.0  # in meters.  max distance between images in same seq.
DEFAULT_MAX_DOP = 20.0  # discard images with GPS DOP greater than this
DEFAULT_MIN_SPEED = 5.0  # discard images when going slower than this (needs track info)


def build_parser():
    """Build the commandline parser."""
    parser = kt.build_parser(__doc__)

    parser.add_argument(
        "images",
        metavar="FILENAME",
        type=str,
        nargs="+",
        help="the images to process",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="remove sidecar files for selected images",
    )
    parser.add_argument(
        "--camera-yaw",
        type=float,
        metavar="DEGREES",
        help="the yaw of the camera (eg. 270 for left viewing camera)",
        default=0.0,
    )
    parser.add_argument(
        "--geofence",
        type=str,
        metavar="LAT,LON,RADIUS",
        help="do not geotag inside this circle. lat, lon in decimal degrees, radius in kilometers",
    )
    parser.add_argument(
        "--min-speed",
        type=float,
        metavar="KM/H",
        help="discard images when going slower than this",
        default=DEFAULT_MIN_SPEED,
    )
    parser.add_argument(
        "--max-time",
        type=float,
        metavar="SECONDS",
        help="max time delta between images in same sequence",
        default=DEFAULT_MAX_TIME,
    )
    parser.add_argument(
        "--max-distance",
        type=float,
        metavar="METERS",
        help="max distance between images in same sequence",
        default=DEFAULT_MAX_DISTANCE,
    )
    parser.add_argument(
        "--max-dop",
        type=float,
        metavar="DOP",
        help="discard images with GPS DOP greater than this",
        default=DEFAULT_MAX_DOP,
    )
    return parser


def main():  # noqa: C901
    """Run this."""
    args = build_parser().parse_args(namespace=kt.args)
    kt.init_logging(args.verbose)

    if args.clean:
        for image in args.images:
            kt.delete_sidecar_file(image)
        return

    args.camera_yaw = ((args.camera_yaw + 180) % 360) - 180  # -180..180

    geotags = []
    for image in args.images:
        logging.debug("Found image: %s" % image)

        gt = kt.read_sidecar_file(image)
        if "sequence_index" in gt:
            del gt["sequence_index"]
        if "tmp_sequence_id" in gt:
            del gt["tmp_sequence_id"]
        geotags.append(gt)

        try:
            f = gpsfix.GPSFix()
            exif = piexif.load(image)
            f.from_exif(exif)

            # direction
            if f.track:
                f.direction = gpsfix.direction(f.track) + args.camera_yaw

            gt.update(f.to_dict())

            gt["projection_yaw"] = args.camera_yaw
            try:
                gt["deviceName"] = (
                    exif["0th"][piexif.ImageIFD.Make].decode()
                    + " "
                    + exif["0th"][piexif.ImageIFD.Model].decode()
                )
            except KeyError:
                pass

        except kt.KartaviewError as e:
            logging.exception(e)

    logging.info(f"Found {len(geotags)} images")

    if args.geofence:
        if m := re.match(r"([-.\d]+),([-.\d]+),(\d+)", args.geofence):
            center = complex(float(m.group(1)), float(m.group(2)))
            radius = int(m.group(3))
            logging.info(f"Applying geofence: Center={center} Radius={radius}km")
            cleared = kt.geofence(geotags, center, radius)
        else:
            logging.error("geofence parameter error")
            return
        logging.info(f"Cleared {cleared} images inside geofence")

    sequences = kt.cut_sequences(
        geotags, args.max_time, args.max_distance, args.max_dop, args.min_speed
    )

    n = 0
    for sequence in sequences:
        tmp_id = str(uuid.uuid4())
        n += len(sequence)
        for gt in sequence:
            gt["tmp_sequence_id"] = tmp_id

    for gt in geotags:
        kt.write_sidecar_file(gt["filename"], gt)

    print(
        "Sequenced %d images into %d sequences (%d images discarded)"
        % (n, len(sequences), len(geotags) - n)
    )


if __name__ == "__main__":
    main()
