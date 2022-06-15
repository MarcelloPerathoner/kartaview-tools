"""
Represents the structure of an MP4 file.

Vantrue intercalates GPS atoms into the stream of video frames. There is also a proprietary atom: a
GPS directory that holds pointers to all GPS atoms.

We are interested in video frames and GPS atoms only.

"""

import logging
import struct
import sys
from typing import List

import kartaview_tools as kt
from .gpsfix import GPSFix


key_frames: List[int] = []
"""List of key frames.  Yet unused."""


class MP4Atom:
    """Represents an atom in a MP4 file."""

    def __init__(self, offset: int):
        """Initialize this."""
        super(MP4Atom, self).__init__()
        self.file_id: int = -1
        """Sequential id of file."""
        self.offset: int = offset
        """Byte offset into file."""


class VideoFrameAtom(MP4Atom):
    """Represents a video frame atom in a MP4 file."""

    def __init__(self, offset: int):
        """Initialize this."""
        super(VideoFrameAtom, self).__init__(offset)

        self.fix_id: int = -1
        """The last fix before this frame."""


class GPSFixAtom(MP4Atom, GPSFix):
    """Represents a non-standard GPS fix frame atom in a Vantrue MP4 file."""

    def __init__(self, offset: int):
        """Initialize this."""
        super(GPSFixAtom, self).__init__(offset)

        self.frame_id: int = -1
        """The last frame before this fix."""


def parse_atom(atoms: List[MP4Atom], mm, offset, offset_end, level):
    """
    Parse an mp4 atom.

    Note: the 'atom' may be the whole file.

    Specs of mp4 file format: https://developer.apple.com/standards/qtff-2001.pdf
    """
    while offset < offset_end:
        atom_size, atom_type = struct.unpack(">I4s", mm[offset : offset + 8])
        atom_end = offset + atom_size

        if kt.args.verbose:
            logging.debug("%08x %s %s" % (offset, level, atom_type))

        if atom_type == b"gps ":
            parse_gps_atom(atoms, mm, offset + 8, atom_end, level + 1)

        if atom_type == b"stsc":
            parse_stsc_atom(atoms, mm, offset + 8, atom_end, level + 1)

        if atom_type == b"stss":
            parse_stss_atom(atoms, mm, offset + 8, atom_end, level + 1)

        if atom_type == b"stco":
            parse_stco_atom(atoms, mm, offset + 8, atom_end, level + 1)

        if atom_type == b"minf":
            parse_minf_atom(atoms, mm, offset + 8, atom_end, level + 1)

        if atom_type in (b"moov", b"trak", b"mdia", b"stbl"):
            # recurse into this ones
            parse_atom(atoms, mm, offset + 8, atom_end, level + 1)

        offset += atom_size


def parse_minf_atom(atoms, mm, offset, offset_end, level):
    """
    Parse an atom of type 'minf' (media information atom).

    We parse this to throw audio tracks out.
    """
    while offset < offset_end:
        atom_size, atom_type = struct.unpack(">I4s", mm[offset : offset + 8])
        atom_end = offset + atom_size

        if kt.args.verbose:
            logging.debug("%08x %s %s" % (offset, level, atom_type))

        if atom_type == b"smhd":
            # yuck! this is an audio track
            return

        if atom_type == b"stbl":
            parse_atom(atoms, mm, offset + 8, atom_end, level + 1)

        offset += atom_size


def parse_stsc_atom(atoms, mm, offset, offset_end, level):
    """
    Parse an atom of type 'stsc' (sample-to-chunk atom).

    This is a sanity check because we assume throughout this program that all chunks contain exactly
    one frame, as do all the videos produced by my camera.
    """
    dummy, entries = struct.unpack(">4sI", mm[offset : offset + 8])
    offset += 8
    for (
        first_chunk,
        samples_per_chunk,
        sample_description_id,
    ) in struct.iter_unpack(">III", mm[offset : offset + 12 * entries]):
        assert samples_per_chunk == 1


def parse_stss_atom(atoms, mm, offset, offset_end, level):
    """
    Parse an atom of type 'stss' (sync sample atom).

    The sync sample atom contains a table of sample numbers. Each entry in the table identifies a
    sample that is a key frame for the media. If no sync sample atom exists, then all the samples
    are key frames.

    ---- https://developer.apple.com/standards/qtff-2001.pdf
    """
    dummy, entries = struct.unpack(">4sI", mm[offset : offset + 8])
    offset += 8
    # unpack always returns a tuple
    for (n,) in struct.iter_unpack(">I", mm[offset : offset + 4 * entries]):
        key_frames.append(n)


def parse_stco_atom(atoms, mm, offset, offset_end, level):
    """
    Parse an atom of type 'stco' (chunk offset atom).

    A chunk offset table consisting of an array of offset values. There is one table entry for each
    chunk in the media. The offset contains the byte offset from the beginning of the data stream to
    the chunk. The table is indexed by chunk number -- the first table entry corresponds to the first
    chunk, the second table entry is for the second chunk, and so on.

    ---- https://developer.apple.com/standards/qtff-2001.pdf
    """
    dummy, entries = struct.unpack(">4sI", mm[offset : offset + 8])
    offset += 8
    # unpack always returns a tuple
    for (offs,) in struct.iter_unpack(">I", mm[offset : offset + 4 * entries]):
        atoms.append(VideoFrameAtom(offs))


def parse_gps_atom(atoms, mm, offset, offset_end, level):
    """Parse an atom of type 'gps ' (undocumented atom).

    This undocumented atom is a directory of the 'free' atoms that contain the GPS data that we
    need.  It is a list of [offset, size] entries.
    """
    dummy, entries = struct.unpack(">II", mm[offset : offset + 8])
    offset += 8
    for offs, size in struct.iter_unpack(">II", mm[offset : offset + 8 * entries]):
        parse_free_atom(atoms, mm, offs + 8, offs + size, 0)


def parse_free_atom(atoms, mm, offset, offset_end, level):
    """Parse an atom of type 'free' (undocumented atom).

    'free' atoms are documented and usually contain free unused space. Vantrue misuses 'free' atoms
    to store GPS fixes.  Vantrue stores these 'free' atoms in the 'mdat' atom between normal chunks
    containing video frames.

    The format is undocumented but it contains a GPRNC statement, which is documented.

    00 4 char4   always 'GPS ', marker
    04 4 int32   always 3f0, version?
    08 4 uint32  HH
    0c 4 uint32  MM
    10 4 uint32  SS
    14 8 char1   'A' or 'V'
    18 8 float32 lat
    20 8 char1   'N' or 'S' or '0'
    28 8 float32 lon
    30 8 char1   'E' or 'W' or '0'
    38 8 float32 speed in knots
    40 8 float32 heading
    48 4 uint32  YY - 2000
    4c 4 uint32  MM
    50 4 uint32  DD
    54 4 int32   accelerometer * 1000
    58 4 int32   "
    5c 4 int32   "
    60 start of $GPRNC sentence

    """
    global next_chunk, fix_id

    if mm[offset : offset + 4] != b"GPS ":
        print("invalid free atom at %x" % offset, file=sys.stderr)
        return

    # try to decode the undocumented stuff
    # (marker, version, hour, minute, second, valid, lat, lat_ref, lon, lon_ref,
    #     speed, heading, day, month, year, z, y, x
    #     ) = struct.unpack ('4sIIII4sd4sd4sddIIIiii', mm[offset:offset + 0x60])

    mm.seek(offset + 0x60)
    gprmc = mm.readline().decode("us-ascii").strip()

    f = GPSFixAtom(offset)
    f.from_GPRMC(gprmc)
    if f.bogus:
        print("invalid $GPRMC entry: %s" % gprmc, file=sys.stderr)
        return

    atoms.append(f)
