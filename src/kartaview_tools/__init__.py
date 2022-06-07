"""This is a package."""

import argparse
import cmath
import collections
import datetime
import glob
import json
import logging
import operator
import os
import re
from typing import List, Dict, Any

from geographiclib.geodesic import Geodesic
import lxml.etree as etree
from lxml.builder import ElementMaker
import requests
from typing import TextIO
from urllib3.util.retry import Retry

from . import catmull_rom as cr
from . import gpsfix
from . import mp4
from .gpsfix import Geotags


CONFIG_FILEPATH = os.getenv(
    "KARTAVIEW_CONFIG_FILEPATH",
    os.path.join(os.path.expanduser("~"), ".config", "kartaview", "credentials.json"),
)

API_RETRIES = 5

# make requests retry on failure

api = requests.Session()
api.mount(
    "https://",
    requests.adapters.HTTPAdapter(
        max_retries=Retry(
            total=API_RETRIES,
            backoff_factor=1,
            method_whitelist=["GET", "POST", "PUT", "DELETE"],
            status_forcelist=[429, 500, 502, 503, 504],
        )
    ),
)

args = argparse.Namespace()


class KartaviewError(Exception):
    """A more general error."""


class ImageUploadError(KartaviewError):
    """Error while uploading an image."""


class SequenceCreationError(KartaviewError):
    """Error while creating a sequence."""


class SequenceClosingError(KartaviewError):
    """Error while closing a sequence."""


class ImageFileInfo:
    """Represents one image file."""

    def __init__(self, filename: str, frame_id: int) -> None:
        """Initialize this."""
        self.filename = filename
        self.frame_id = frame_id
        self.timestamp: datetime.datetime | None = None
        """Interpolated timestamp."""


def interpolate_coords(fixes: List[mp4.GPSFixAtom], images: List[ImageFileInfo]):
    """
    Iterate over all images and interpolate their GPS positions.

    The image position is interpolated using a centripetal catmull-rom spline, which needs four GPS
    fixes around the frame to interpolate.

    Images and fixes must already be sorted by timestamp.
    """
    p: collections.deque[mp4.GPSFixAtom] = collections.deque(maxlen=4)
    """A sliding window of 4 fixes around the frame to interpolate.
    The frame being in the half-open interval [p[1]..p[2]) """

    fixes_iter = iter(fixes)
    interpolated = 0

    for image in images:
        assert image.timestamp
        logging.debug(f"Processing {image.filename}")

        try:
            while not p[3] or image.timestamp > p[2].timestamp:
                p.append(next(fixes_iter))

            i = gpsfix.GPSFix()
            i.timestamp = image.timestamp

            # interpolate GPS position
            # need 4 fixes for catmull interpolation
            if p[1].timestamp <= image.timestamp < p[2].timestamp:
                if p[1].timestamp == image.timestamp:
                    i.coord = p[2].coord
                    i.track = p[2].track
                    i.direction = p[2].direction
                else:
                    elapsed = p[2].timestamp - p[1].timestamp
                    t = (image.timestamp - p[1].timestamp) / elapsed
                    # interpolate GPS position
                    i.coord = cr.ccatmull(
                        t, p[0].coord, p[1].coord, p[2].coord, p[3].coord
                    )
                    # interpolate GPS track
                    if p[0].track and p[1].track and p[2].track and p[3].track:
                        i.track = cr.ccatmull(
                            t, p[0].track, p[1].track, p[2].track, p[3].track
                        )
                    if i.track:
                        i.direction = (
                            gpsfix.rad2deg(cmath.phase(i.track)) + args.camera_yaw
                        )
                interpolated += 1
        except StopIteration:
            pass
        logging.info(f"Interpolated {interpolated} image positions")


def interpolate_track(fixes: List[mp4.GPSFixAtom]) -> None:
    """Interpolate a heading for each fix using the fix before and the fix after."""
    p: collections.deque[mp4.GPSFixAtom] = collections.deque([None, None, None], 3)  # type: ignore
    """A sliding window of 3 fixes for calculating catmull tangents at nodes"""
    interpolated = 0

    for fix in fixes:
        p.append(fix)
        if p[0]:
            p1_tangent = cr.catmull_tangent(p[0].coord, p[1].coord, p[2].coord)
            head = heading(p[1].coord, p[1].coord + p1_tangent)
            # To get the exact speed we'd have to calculate the arc length of the spline, which
            # implies elliptic curve integrals etc.  Too complicated.  So we simply approximate
            # the arc length as the linear distance between the positions of the gps fixes.
            m = distance(p[1].coord, p[2].coord)
            elapsed = p[2].timestamp - p[1].timestamp
            s = elapsed.seconds + elapsed.microseconds / 1_000_000.0
            kmh = m / s * 3.6
            if args.verbose:
                logging.debug(
                    f"meter={m} seconds={s} km/h={kmh} gps_speed={abs(p[1].track)}"
                )
            p[1].track = gpsfix.polar(kmh, gpsfix.deg2rad(head))
            interpolated += 1

    if interpolated:
        logging.info(f"Interpolated {interpolated} GPS headings")


def write_gpx(fp: TextIO, fixes: List[mp4.GPSFixAtom]) -> None:
    """Write a GPX file of all GPS fixes."""
    NS_GPX = "http://www.topografix.com/GPX/1/1"
    E = ElementMaker(namespace=NS_GPX, nsmap={None: NS_GPX})

    track_points = []
    for o in fixes:
        if o.timestamp and o.coord:
            track_points.append(
                E.trkpt(
                    E.time(o.timestamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ")),
                    lat="%.6f" % o.coord.real,
                    lon="%.6f" % o.coord.imag,
                )
            )
    gpx = E.gpx(
        E.trk(E.trkseg(*track_points)), version="1.1", creator="kartaview_tools"
    )
    fp.write(etree.tostring(gpx, pretty_print=True, encoding="unicode"))


def build_parser(description: str) -> argparse.ArgumentParser:
    """Build the commandline parser."""
    parser = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,  # don't wrap my description
        fromfile_prefix_chars="@",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        dest="verbose",
        action="count",
        help="increase output verbosity",
        default=0,
    )
    return parser


def init_logging(verbose):
    """Init the logger."""
    level = logging.ERROR
    if verbose == 1:
        level = logging.WARNING
    if verbose == 2:
        level = logging.INFO
    if verbose > 2:
        level = logging.DEBUG
    logging.basicConfig(level=level, format="%(message)s")


def geofence(geotags: List[Geotags], center: complex, radius: float) -> int:
    """
    Clear the GPS position if it is inside the geofence.

    May be used to protect the user's privacy.

    -- center real=lat,imag=lon
    -- radius in km

    returns count of cleared images
    """
    cleared = 0
    for gt in geotags:
        if (
            "lat" in gt
            and "lon" in gt
            and distance(center, complex(gt["lat"], gt["lon"])) / 1000 < radius
        ):
            del gt["lat"]
            del gt["lon"]
            cleared += 1
    return cleared


def cut_sequences(
    geotags: List[Geotags],
    max_time: float,
    max_dist: float,
    max_dop: float,
    min_speed: float,
) -> List[List[Geotags]]:
    """Build image sequences.

    max_time in s      above this a new sequence will be started
    max_dist in m      above this a new sequence will be started
    max_dop            greater than this disqualifies
    min_speed in km/h  slower than this disqualifies
    """

    def filt(gt: Geotags) -> bool:
        if "timestamp" not in gt:
            logging.info("%s has no datetime", gt["filename"])
            return False

        if "lat" not in gt:
            logging.info("%s has no GPS coordinates", gt["filename"])
            return False

        if gt.get("dop", 0.0) > max_dop:
            logging.info("%s exceeds max GPS DOP", gt["filename"])
            return False

        if (speed := gt.get("speed", 0.0)) < min_speed:
            logging.info(
                f"{gt['filename']}s disqualified as duplicate. (Speed={speed:.2f} < {min_speed})"
            )
            return False

        return True

    geotags = sorted(filter(filt, geotags), key=operator.itemgetter("timestamp"))
    sequence_index = 0
    geotags[0]["sequence_index"] = sequence_index
    sequence = [geotags[0]]
    result = []

    for a, b in zip(geotags, geotags[1:]):
        sequence_index += 1
        dta = datetime.datetime.fromisoformat(a["timestamp"])
        dtb = datetime.datetime.fromisoformat(b["timestamp"])
        elapsed = (dtb - dta).total_seconds()
        dist = distance(complex(a["lat"], a["lon"]), complex(b["lat"], b["lon"]))
        if elapsed > max_time or dist > max_dist:
            print("Sequence of: %d images" % len(sequence))
            print(
                "New sequence: Elapsed=%.2f/%.2f, Distance=%.2f/%.2f"
                % (elapsed, max_time, dist, max_dist)
            )
            # start a new sequence
            result.append(sequence)
            sequence = []
            sequence_index = 0
        b["sequence_index"] = sequence_index
        sequence.append(b)

    print("Sequence of: %d images" % len(sequence))
    result.append(sequence)
    return result


def distance(p0: complex, p1: complex) -> float:
    """Return the geodesic distance from p0 to p1 in meters.

    See: https://geographiclib.sourceforge.io/Python/doc/code.html
    """
    return Geodesic.WGS84.Inverse(
        p0.real, p0.imag, p1.real, p1.imag, Geodesic.DISTANCE
    )["s12"]


def heading(p0: complex, p1: complex) -> float:
    """Return the geodesic heading from p0 to p1 in decimal degrees."""
    return Geodesic.WGS84.Inverse(p0.real, p0.imag, p1.real, p1.imag, Geodesic.AZIMUTH)[
        "azi1"
    ]


def read_config_file(filename: str = CONFIG_FILEPATH) -> Dict[str, Any]:
    """Get the authorization tokens for the kartaview API.

    Returns: {
        "kartaview": {
            "access_token": "hexdigits64",
            "driver_type": "DEDICATED",
            "externalUserId": "69",
            "full_name": "Zaphod Beeblebrox",
            "id": "42",
            "role": "ROLE_USER",
            "type": "user",
            "username": "zaphod-beeblebrox"
        },
        "osm": {
            "oauth_token": "char40",
            "oauth_token_secret": "char40"
        }
    }
    """
    try:
        with open(CONFIG_FILEPATH, "r") as config:
            args.config = json.load(config)
            return args.config
    except (IOError,) as e:
        raise KartaviewError() from e


def get_auth_token() -> str:
    """Get the authorization token from the configuration file."""
    return args.config["kartaview"]["access_token"]


def ffmpeg_glob(mpegs: str) -> List[str]:
    """Find files matching an ffmpeg-style glob.

    eg. frames/img%05d.jpeg -> [frames/img00000.jpeg, frames/img00010.jpeg, ...]
    """
    images: List[str] = []
    fileglobs = to_glob(mpegs)
    regex = to_regex(mpegs)
    for filename in glob.glob(fileglobs):
        if re.match(regex, filename):
            images.append(filename)
    return sorted(images)


def to_glob(files: str) -> str:
    """Turn a ffmpeg files parameter into a unix glob.

    tmp/img%05d.jpg -> tmp/img?????.jpg
    """

    def mkglob(matchobj):
        return "?" * int(matchobj.group(1))

    return re.sub(r"%(\d+)d", mkglob, files)


def to_regex(files: str) -> str:
    r"""Turn a ffmpeg files parameter into a regular expression.

    tmp/img%05d.jpg -> tmp/img(\d{5}).jpg
    """

    def mkre(matchobj):
        return r"(\d{%d})" % int(matchobj.group(1))

    return re.sub(r"%(\d+)d", mkre, files)


def write_config_file(credentials: str, filename: str = CONFIG_FILEPATH) -> None:
    """Write the configuration file."""
    # file contains sensitive information so make it chmod 600
    with open(
        os.open(filename, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600), "w"
    ) as fp:
        fp.write(json.dumps(credentials, ensure_ascii=False, sort_keys=True, indent=4))


def get_sidecar_filename(image_filename: str) -> str:
    """Return the sidecar filename for the image."""
    return image_filename + ".kv"


def read_sidecar_file(image_filename: str) -> Geotags:
    """Read the sidecar file for an image."""
    try:
        sidecar = get_sidecar_filename(image_filename)
        with open(sidecar, "r") as fp:
            gt = json.loads(fp.read())
            gt["filename"] = image_filename
            return gt
    except OSError:
        return {"filename": image_filename}
    except json.decoder.JSONDecodeError as e:
        raise KartaviewError(
            "{sidecar} JSON syntax error\n".format(sidecar=sidecar)
        ) from e


def write_sidecar_file(image_filename: str, gt: Geotags) -> None:
    """Write the sidecar file for an image."""
    with open(get_sidecar_filename(image_filename), "w") as fp:
        d = dict(gt)
        if "filename" in d:
            d["filename"] = os.path.basename(d["filename"])
        fp.write(json.dumps(d, ensure_ascii=False, sort_keys=True, indent=4))


def delete_sidecar_file(image_filename: str) -> None:
    """Delete the sidecar file for an image."""
    try:
        os.remove(get_sidecar_filename(image_filename))
    except OSError:
        pass
