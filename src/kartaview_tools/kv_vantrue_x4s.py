#!/usr/bin/env python3

"""
Extract GPS data from video files produced by the Vantrue OnDash X4S dashcam.

To process a video into geotagged files, first extract the images from the video file using ffmpeg:

  ffmpeg -i 20220131_081559_0042_T_A.MP4 -frame_pts 1 0042A/%08d.jpg

Then patch the GPS data into the images:

  kv_vantrue_x4s.py -i 20220131_081559_0042_T_A.MP4 0042A/%08d.jpg

  kv_vantrue_x4s.py -i 20220131_081559_0042_T_B.MP4 --camera-yaw=180 0042B/%08d.jpg

The images can now be used in JOSM, etc.

Process multiple sequential videos (the last GPS fixes from the first video will be used to
interpolate the first frames of the second video and vice versa):

  kv_vantrue_x4s.py -i *0042_T_A.MP4 -i *0043_T_A.MP4 0042/%08d.jpg 0043/%08d.jpg

Extract a GPX track to stdout or file (also works with multiple videos):

  kv_vantrue_x4s.py -i 20220131_081559_0042_T_A.MP4 --gpx

  kv_vantrue_x4s.py -i 20220131_081559_0042_T_A.MP4 --gpx=track.gpx

Print all raw GPRMC statements found (also works with multiple videos):

  kv_vantrue_x4s.py -i 20220131_081559_0042_T_A.MP4 --gprmc

"""

import argparse
import datetime
import glob
import logging
import mmap
import operator
import re
import sys
from typing import List, Optional

import piexif

import kartaview_tools as kt
from kartaview_tools import mp4


class VideoFileInfo:
    """Represents one video file."""

    def __init__(self, file_id: int, filename: str, atoms: List[mp4.MP4Atom]):
        """
        Calculate the true video rate, the GPS rate and the GPS timestamp of the first frame.

        Uses the GPS atoms in the file. Must account for invalid atoms due to GPS startup time.
        """
        self.file_id = file_id
        self.filename = filename
        self.atoms: List[mp4.MP4Atom] = atoms
        self.frames: List[mp4.VideoFrameAtom] = []
        self.fixes: List[mp4.GPSFixAtom] = []

        last: Optional[mp4.GPSFixAtom] = None
        "the last seen GPS atom with a valid timestamp"

        for atom in atoms:
            atom.file_id = file_id
            if isinstance(atom, mp4.GPSFixAtom):
                atom.frame_id = len(self.frames) - 1
                if atom.timestamp:
                    if last:
                        if last.timestamp == atom.timestamp:
                            logging.warning(
                                f"Duplicate timestamp in GPS fix {atom.timestamp}, discarding."
                            )
                            continue
                        if last.coord and atom.coord and last.coord == atom.coord:
                            logging.warning(
                                f"Duplicate position in GPS fix {atom.coord}, discarding."
                            )
                            continue
                    self.fixes.append(atom)
                    last = atom
            if isinstance(atom, mp4.VideoFrameAtom):
                atom.fix_id = len(self.fixes) - 1
                self.frames.append(atom)

        self.frame_cnt = len(self.frames)
        self.fix_cnt = len(self.fixes)
        if self.fix_cnt < 2:
            # too few fixes
            return

        first = self.fixes[0]
        last = self.fixes[-1]  # make mypy happy

        seconds = (
            (last.timestamp - first.timestamp) / datetime.timedelta(microseconds=1)
        ) / 1_000_000.0
        frames = last.frame_id - first.frame_id
        fixes = len(self.fixes)

        self.frame_rate = round(
            frames / seconds
        )  # one of 1, 5, 10, 30, 60 on Vantrue devices
        self.gps_rate = fixes / seconds
        self.key_rate = round(self.frame_cnt / len(mp4.key_frames))
        self.start_time = first.timestamp - datetime.timedelta(
            seconds=first.frame_id / self.frame_rate
        )

    def interpolate_timestamps(self, images: List[kt.ImageFileInfo]):
        """
        Iterate over all images and interpolate their timestamps.

        Iterate over all images and calculate a timestamp for each image using the GPS fixes. Time
        is interpolated linearly between fixes. We also extrapolate the time before the first fix
        and after the last fix in the video file.
        """
        for image in images:
            frame: int = image.frame_id
            prev_fix: int = self.frames[frame].fix_id
            next_fix: int = prev_fix + 1

            # interpolate timestamp
            if 1 <= next_fix < self.fix_cnt:
                # need 2 fixes for linear interpolation
                p1, p2 = self.fixes[next_fix - 1 : next_fix + 1]

                if p2.frame_id == frame:
                    image.timestamp = p2.timestamp
                t = (frame - p1.frame_id) / float(p2.frame_id - p1.frame_id)

                if p1.timestamp and p2.timestamp:
                    image.timestamp = p1.timestamp + t * (p2.timestamp - p1.timestamp)

            # extrapolate timestamp
            if not image.timestamp:
                image.timestamp = self.start_time + datetime.timedelta(
                    seconds=frame / self.frame_rate
                )

        logging.info("Interpolated %d image timestamps" % len(images))


def build_parser() -> argparse.ArgumentParser:
    """Build the commandline parser."""
    parser = kt.build_parser(__doc__)

    parser.add_argument(
        "mpegs",
        metavar="FFMPEG",
        nargs="*",
        type=str,
        help="patch GPS data into these JPEG files. Same format as ffmpeg uses.",
    )
    parser.add_argument(
        "-i",
        "--input",
        dest="videos",
        metavar="VIDEO.MP4",
        nargs=1,
        action="extend",
        type=str,
        help="the .mp4 file to process. Multiple occurences allowed.",
    )
    parser.add_argument(
        "--gpx",
        metavar="OUTFILE.GPX",
        nargs="?",
        type=argparse.FileType("w"),
        const=sys.stdout,
        help="output GPX format to stdout or file",
    )
    parser.add_argument(
        "--gprmc",
        metavar="OUTFILE.TXT",
        nargs="?",
        type=argparse.FileType("w"),
        const=sys.stdout,
        help="output a list of GPRMC statements to stdout or file",
    )
    parser.add_argument(
        "--camera-yaw",
        type=float,
        metavar="DEGREES",
        default=0.0,
        help="the yaw of the camera (eg. 270 for left viewing camera)",
    )
    parser.add_argument(
        "--interpolate-track",
        action="store_true",
        help="interpolate GPS heading and speed (overwrites tracks by GPS)",
    )
    parser.add_argument(
        "--frame-offset",
        default=0,
        type=int,
        help="(experts only) offset GPS fixes by N frames (default: 0)",
    )
    parser.add_argument(
        "--starttime",
        metavar="OUTFILE.TXT",
        nargs="?",
        type=argparse.FileType("w"),
        const=sys.stdout,
        help="output the extrapolated GPS timestamp of the first frame in a format fit for exiftool",
    )
    parser.add_argument(
        "--starttime-offset",
        metavar="SECONDS",
        type=int,
        default=0,
        help="the offset to add to the starttime output in seconds",
    )
    parser.add_argument(
        "--framerate",
        metavar="OUTFILE.TXT",
        nargs="?",
        type=argparse.FileType("w"),
        const=sys.stdout,
        help="output the true framerate, eg. 10 == ten frames per second",
    )
    parser.add_argument(
        "--keyframerate",
        metavar="OUTFILE.TXT",
        nargs="?",
        type=argparse.FileType("w"),
        const=sys.stdout,
        help="output the true keyframerate, eg. 10 == ten frames per key frame",
    )
    parser.add_argument(
        "--gpsrate",
        metavar="OUTFILE.TXT",
        nargs="?",
        type=argparse.FileType("w"),
        const=sys.stdout,
        help="output the number of GPS fixes per second",
    )

    return parser


def main():  # noqa: C901
    """Run this."""
    parser = build_parser()
    args = parser.parse_args(namespace=kt.args)
    if not args.videos:
        parser.print_usage()
        sys.exit()
    kt.init_logging(args.verbose)

    videos: List[VideoFileInfo] = []
    all_fixes: List[mp4.GPSFixAtom] = []

    for n, video in enumerate(args.videos):
        atoms = []
        with open(video, "r+b") as fp:
            logging.info("Processing video: %s" % video)
            with mmap.mmap(fp.fileno(), 0) as mm:
                mp4.parse_atom(atoms, mm, 0, mm.size(), 0)
        atoms = sorted(atoms, key=operator.attrgetter("offset"))
        vfi = VideoFileInfo(n, video, atoms)
        videos.append(vfi)
        all_fixes.extend(vfi.fixes)

    # Throw invalid fixes out and sort for interpolation.  This is necessary because the video files
    # given to us may not be in cronological order.
    sort_by_timestamp = operator.attrgetter("timestamp")
    all_fixes = sorted(
        filter(operator.attrgetter("coord"), all_fixes), key=sort_by_timestamp
    )

    # Optionally interpolate a heading for each fix
    if args.interpolate_track:
        kt.interpolate_track(all_fixes)

    if args.mpegs:
        images: List[kt.ImageFileInfo] = {}
        for n, mpegs in enumerate(args.mpegs):
            fileglobs = kt.to_glob(mpegs)
            regex = kt.to_regex(mpegs)
            logging.info(f"Processing files: {fileglobs}")

            for filename in sorted(glob.glob(fileglobs)):
                if m := re.match(regex, filename):
                    # frame according to filename + offset
                    frame = int(m.group(1)) + args.frame_offset
                    image = kt.ImageFileInfo(filename, frame)
                    images[frame] = image

            # the mp4 arguments must be in the same order as the ffmpeg arguments
            videos[n].interpolate_timestamps(images)

        images = sorted(images, key=sort_by_timestamp)

        # Interpolate image coordinates
        kt.interpolate_coords(all_fixes, images)

        # Patch the image files
        for image in images:
            exif = piexif.load(image.filename)
            image.update_exif(exif)
            exif["0th"][piexif.ImageIFD.Make] = "Vantrue"
            exif["0th"][piexif.ImageIFD.Model] = "OnDash X4S"
            piexif.insert(piexif.dump(exif), image.filename)

        return

    if args.gprmc:
        for o in all_fixes:
            args.gprmc.write(o.gprmc + "\n")

    if args.gpx:
        kt.write_gpx(args.gpx, all_fixes)

    if args.starttime:
        td = datetime.timedelta(seconds=args.starttime_offset)
        args.starttime.write(
            (videos[0].start_time + td).strftime("%Y:%m:%d %H:%M:%S.%f") + "\n"
        )

    if args.framerate:
        args.framerate.write("%d\n" % videos[0].frame_rate)

    if args.keyframerate:
        args.keyframerate.write("%d\n" % videos[0].key_rate)

    if args.gpsrate:
        args.gpsrate.write("%d\n" % videos[0].gps_rate)


if __name__ == "__main__":
    main()
