"""The GPSFix class. Converts a GPS fix from and to various formats."""

import cmath
import datetime
from dateutil import tz
import math
import re
from typing import Any, Dict, Tuple

import piexif


Geotags = Dict[str, Any]
GPSRational = Tuple[int, int]
GPSRational3 = Tuple[GPSRational, GPSRational, GPSRational]
ExifIFD = Dict[int, Any]
ExifType = Dict[str, ExifIFD]


def rad2deg(rad: float) -> float:
    """Convert radians into degrees in the range 0..360."""
    return ((180 * rad / math.pi) + 360) % 360


def deg2rad(deg: float) -> float:
    """Convert degrees into radians."""
    return deg * math.pi / 180


def polar(r: float, rad: float) -> complex:
    """Convert polar coordinates into complex number. angle in radians."""
    return r * cmath.exp(1j * rad)


def latlon_to_complex(gt: Geotags) -> complex:
    """Convert geotags lat/lon to complex number."""
    return complex(gt["lat"], gt["lon"])


def direction(track: complex) -> float:
    """Return the track direction in degrees in the range 0..360."""
    return rad2deg(cmath.phase(track))


def gps_rational(value: GPSRational) -> float:
    """Decode an EXIF rational value to float."""
    return float(value[0]) / float(value[1])


def gps_coordinate(coord: GPSRational3, reference: bytes) -> float:
    """Decode an EXIF GPS coordinate to decimal degrees in a float.

    GPS Coordinates are stored as 3 rationals.
    """
    sign = 1 if reference in b"NE" else -1
    degrees, minutes, seconds = map(gps_rational, coord)
    return sign * (degrees + minutes / 60 + seconds / 3600)


def gps_datetime(date: bytes, time: GPSRational3) -> datetime.datetime:
    """Decode an EXIF GPSTimeStamp to datetime."""
    utc = datetime.datetime.strptime(date.decode(), "%Y:%m:%d")
    utc.replace(tzinfo=tz.tzutc())
    hours, minutes, seconds = map(gps_rational, time)
    return utc + datetime.timedelta(seconds=hours * 3600 + minutes * 60 + seconds)


def exif_datetime(value: bytes, subseconds: str) -> datetime.datetime:
    """Decode exif datetime as datetime.

    Exif datetimes are assumed to be in the local timezone.
    """
    local = datetime.datetime.strptime(value.decode(), "%Y:%m:%d %H:%M:%S")
    local.replace(tzinfo=tz.tzlocal())
    utc = local.astimezone(tz.tzutc())
    return utc + datetime.timedelta(seconds=float("0." + subseconds))


class GPSFix:
    """A class to represent on GPS fix and conversions to and from various formats."""

    def __init__(self):
        """Initialize this."""
        super(GPSFix, self).__init__()
        self.rmc: str = None
        "P or N for GPRMC or GNRMC"
        self.bogus: bool = False
        "set if the GPRMC could not be scanned"
        self.valid: bool = False
        "validity according to the GPRMC statement A or V"
        self.mode: str = None
        self.timestamp: datetime.datetime = None
        "utc"
        self.coord: complex = None  # NOTE: real = lat, imag = lon
        "real = lat, imag = lon"
        self.track: complex = None
        "r = speed in km/h, phi = heading"
        self.accuracy: float = None
        "horizontal accuracy in m"
        self.altitude: float = None
        "altitude in m WGS84 datum"
        self.direction: float = None
        "direction of the camera when picture was taken 0..360"
        self.dop: float = None
        "dilution of precision because of poor satellite geometry"

    def to_dict(self) -> Geotags:
        """Return values as dict for conversion to JSON."""
        d: Geotags = dict()
        if self.timestamp:
            d["timestamp"] = self.timestamp.isoformat()
        if self.coord:
            d["lat"] = round(self.coord.real, 8)
            d["lon"] = round(self.coord.imag, 8)
        if self.track:
            d["speed"] = round(abs(self.track), 2)
            d["heading"] = round(rad2deg(cmath.phase(self.track)), 2)
        if self.accuracy:
            d["accuracy"] = round(self.accuracy, 3)
        if self.altitude:
            d["altitude"] = round(self.altitude, 2)
        if self.direction:
            d["direction"] = round(self.direction % 360, 2)
        if self.dop:
            d["dop"] = round(self.dop, 2)
        return d

    def from_dict(self, d: Geotags) -> None:
        """Load from dict."""
        if "timestamp" in d:
            self.timestamp = datetime.datetime.fromisoformat(d["timestamp"])
        if "lat" in d and "lon" in d:
            self.coord = complex(d["lat"], d["lon"])
        if "speed" in d and "heading" in d:
            self.track = polar(d["speed"], deg2rad(d["heading"]))
        if "accuracy" in d:
            self.accuracy = float(d["accuracy"])
        if "altitude" in d:
            self.altitude = float(d["altitude"])
        if "direction" in d:
            self.direction = float(d["direction"])
        if "dop" in d:
            self.dop = float(d["dop"])

    def from_GPRMC(self, gprmc: str) -> None:
        """Decode a GPRMC statement.

        See: https://docs.novatel.com/OEM7/Content/Logs/GPRMC.htm
        """

        def fix_datetime(date: str, time: str) -> datetime.datetime | None:
            md = re.match(r"(\d\d)(\d\d)(\d\d)", date)
            mt = re.match(r"(\d\d)(\d\d)(\d\d)\.(\d+)", time)
            if md and mt:
                return datetime.datetime(
                    year=int(md.group(1)) + 2000,
                    month=int(md.group(2)),
                    day=int(md.group(3)),
                    hour=int(mt.group(1)),
                    minute=int(mt.group(2)),
                    second=int(mt.group(3)),
                    microsecond=int(mt.group(4).rjust(6, "0")),
                    tzinfo=tz.tzutc(),  # datetime.timezone.utc
                )
            return None

        def fix_deg(s: str, sign: str) -> float:
            coord = float(s)
            minutes = coord % 100.0
            degrees = coord - minutes
            return (degrees / 100.0 + minutes / 60.0) * 1 if s in ("N", "E") else -1

        def fix_speed(speed: str) -> float:
            return float(speed) * 1.852  # knots -> kmh

        self.gprmc = gprmc
        if m := re.match(
            r"\$(G[PN]RMC),([.\d]*),([AV]),([.\d]*),([NS]?),([.\d]*),([EW]?),([.\d]*),([.\d]*),([\d]*),([.\d]*),([EW]?),([ADEMN])\*(\w\w)",
            gprmc,
        ):
            self.rmc = m.group(1)
            self.valid = m.group(3) == "A"
            self.mode = m.group(13)
            if m.group(10) and m.group(2):
                self.timestamp = fix_datetime(m.group(10), m.group(2))  # type: ignore
            if m.group(4) and m.group(6):
                self.coord = complex(
                    fix_deg(m.group(4), m.group(5)), fix_deg(m.group(6), m.group(7))
                )
            if m.group(8) and m.group(9):
                self.track = polar(fix_speed(m.group(8)), deg2rad(float(m.group(9))))
        else:
            self.bogus = True

    def get_exif_timestamp(self, exif: ExifType) -> datetime.datetime | None:
        """
        Get the timestamp from EXIF.

        Prefer GPS time because it is guaranteed to be UTC and exact.
        """
        exif_image: ExifIFD = exif["0th"]
        exif_exif: ExifIFD = exif["Exif"]
        exif_gps: ExifIFD = exif["GPS"]

        if (
            piexif.GPSIFD.GPSDateStamp in exif_gps
            and piexif.GPSIFD.GPSTimeStamp in exif_gps
        ):
            return gps_datetime(
                exif_gps[piexif.GPSIFD.GPSDateStamp],
                exif_gps[piexif.GPSIFD.GPSTimeStamp],
            )
        else:
            subseconds = ""
            for f in [
                piexif.ExifIFD.SubSecTime,
                piexif.ExifIFD.SubSecTimeOriginal,
                piexif.ExifIFD.SubSecTimeDigitized,
            ]:
                if f in exif_exif:
                    subseconds = exif_exif[f]
                    break

            if piexif.ImageIFD.DateTime in exif_image:
                return exif_datetime(exif_image[piexif.ImageIFD.DateTime], subseconds)
            for f in [
                piexif.ExifIFD.DateTimeOriginal,
                piexif.ExifIFD.DateTimeDigitized,
            ]:
                if f in exif_exif:
                    return exif_datetime(exif_exif[f], subseconds)
            return None

    def from_exif(self, exif: ExifType) -> None:
        """
        Read GPSFix from EXIF dict.

        Exif standard 2.3: https://www.cipa.jp/std/documents/e/DC-008-2012_E.pdf
        """

        def set_from(attr: str, exif_field: int) -> None:
            if exif_field in exif_gps:
                self.__setattr__(attr, gps_rational(exif_gps[exif_field]))

        exif_gps: ExifIFD = exif["GPS"]

        # coordinate
        try:
            self.coord = complex(
                gps_coordinate(
                    exif_gps[piexif.GPSIFD.GPSLatitude],
                    exif_gps[piexif.GPSIFD.GPSLatitudeRef],
                ),
                gps_coordinate(
                    exif_gps[piexif.GPSIFD.GPSLongitude],
                    exif_gps[piexif.GPSIFD.GPSLongitudeRef],
                ),
            )
        except KeyError:
            pass

        # timestamp
        self.timestamp = self.get_exif_timestamp(exif)  # type: ignore

        # track
        try:
            self.track = polar(
                gps_rational(exif_gps[piexif.GPSIFD.GPSSpeed]),
                deg2rad(gps_rational(exif_gps[piexif.GPSIFD.GPSTrack])),
            )
        except KeyError:
            pass

        # altitude
        try:
            sign = -1 if exif_gps.get(piexif.GPSIFD.GPSAltitudeRef, 0) else 1
            self.altitude = sign * gps_rational(exif_gps[piexif.GPSIFD.GPSAltitude])
        except KeyError:
            pass

        set_from("accuracy", piexif.GPSIFD.GPSHPositioningError)
        set_from("direction", piexif.GPSIFD.GPSImgDirection)
        set_from("dop", piexif.GPSIFD.GPSDOP)

    def write_exif(self, filename: str) -> None:
        """Write the GPS data to a JPEG file."""
        exif = piexif.load(filename)
        self.update_exif(exif)
        piexif.insert(piexif.dump(exif), filename)

    def update_exif(self, exif: ExifType) -> None:
        """Update the GPS data in an EXIF dictionary.

        Exif standard 2.3: https://www.cipa.jp/std/documents/e/DC-008-2012_E.pdf
        """
        MIL = 1_000_000

        def ratio(n: float, denom: int = 1) -> GPSRational:
            """Convert a float into a GPS rational."""
            return int(n * denom), int(denom)

        def coord2rationals(decimal_degrees: float) -> GPSRational3:
            degrees, minutes = divmod(decimal_degrees * 60, 60)
            return ratio(degrees), ratio(minutes, MIL), ratio(0)

        def get_from(attr: str, exif_field: int) -> None:
            if value := getattr(self, attr, None):
                exif_gps[exif_field] = ratio(value, 1000)

        exif_image: ExifIFD = exif["0th"]
        exif_exif: ExifIFD = exif["Exif"]
        exif_gps: ExifIFD = exif["GPS"]
        # scrub GPS data
        exif_gps.clear()

        exif_gps[piexif.GPSIFD.GPSVersionID] = (2, 3, 0, 0)

        if self.timestamp:
            utc = self.timestamp
            localtime = self.timestamp.astimezone(tz.tzlocal())
            exif_gps[piexif.GPSIFD.GPSDateStamp] = utc.strftime("%Y:%m:%d")
            exif_gps[piexif.GPSIFD.GPSTimeStamp] = (
                ratio(utc.hour),
                ratio(utc.minute),
                ratio(utc.second + utc.microsecond / MIL, MIL),
            )

            dt = localtime.strftime("%Y:%m:%d %H:%M:%S")
            subsec = "%06d" % localtime.microsecond

            exif_image[piexif.ImageIFD.DateTime] = dt
            exif_exif[piexif.ExifIFD.DateTimeOriginal] = dt
            exif_exif[piexif.ExifIFD.DateTimeDigitized] = dt

            exif_exif[piexif.ExifIFD.SubSecTime] = subsec
            exif_exif[piexif.ExifIFD.SubSecTimeOriginal] = subsec
            exif_exif[piexif.ExifIFD.SubSecTimeDigitized] = subsec

        if self.coord:
            exif_gps[piexif.GPSIFD.GPSLatitude] = coord2rationals(abs(self.coord.real))
            exif_gps[piexif.GPSIFD.GPSLongitude] = coord2rationals(abs(self.coord.imag))
            exif_gps[piexif.GPSIFD.GPSLatitudeRef] = "N" if self.coord.real > 0 else "S"
            exif_gps[piexif.GPSIFD.GPSLongitudeRef] = (
                "E" if self.coord.imag > 0 else "W"
            )

        if self.track:
            exif_gps[piexif.GPSIFD.GPSSpeed] = ratio(abs(self.track), 100)
            exif_gps[piexif.GPSIFD.GPSSpeedRef] = "K"
            exif_gps[piexif.GPSIFD.GPSTrack] = ratio(
                rad2deg(cmath.phase(self.track)), 100
            )
            exif_gps[piexif.GPSIFD.GPSTrackRef] = "T"

        if self.altitude:
            ref = 0 if self.altitude >= 0 else 1
            exif_gps[piexif.GPSIFD.GPSAltitude] = ratio(abs(self.altitude), 100)
            exif_gps[piexif.GPSIFD.GPSAltitudeRef] = ref

        get_from("accuracy", piexif.GPSIFD.GPSHPositioningError)
        get_from("direction", piexif.GPSIFD.GPSImgDirection)
        get_from("dop", piexif.GPSIFD.GPSDOP)
