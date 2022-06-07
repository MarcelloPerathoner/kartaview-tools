"""The OpenStreetCam 2.0 API.  This is as yet *UNTESTED*."""

import io
import json
import logging
from typing import Dict

import piexif
import requests


import kartaview_tools as kt


API_ENDPOINT = "https://api.openstreetcam.org/2.0"

SEQUENCE_ENDPOINT = API_ENDPOINT + "/sequence/"
PHOTO_ENDPOINT = API_ENDPOINT + "/photo/"

API_TIMEOUT = 60.0


def create_sequence(args, parameters: Dict = {}):
    """Create a new sequence on the server."""
    try:
        headers = {"X-Auth-Token": kt.get_auth_token()}
        if args.dry_run:
            if args.verbose:
                logging.info("POST %s" % SEQUENCE_ENDPOINT)
                logging.info("params: %s" % parameters)
            return 0
        else:
            r = requests.post(
                SEQUENCE_ENDPOINT, data=parameters, headers=headers, timeout=API_TIMEOUT
            )
            jso = r.json()
            if args.verbose:
                logging.info(json.dumps(jso, ensure_ascii=False, indent=4))
            r.raise_for_status()
            return jso["result"]["data"]["id"]
    except requests.exceptions.RequestException as e:
        raise kt.SequenceCreationError("server error while creating sequence\n") from e


def close_sequence(args, sequence_id):
    """Close a sequence on the server."""
    if not args.dry_run:
        headers = {"X-Auth-Token": kt.get_auth_token()}
        requests.get(SEQUENCE_ENDPOINT + str(sequence_id) + "/finish", headers=headers)


def upload_image(args, sequence_id, geotags):
    """Upload one image to the server."""
    filename = geotags["filename"]

    with open(filename, "rb") as fpin:
        exif_dict = piexif.load(fpin.read())

        # insert exif data into memory buffer
        fpout = io.BytesIO()
        fpin.seek(0)
        try:
            piexif.insert(piexif.dump(exif_dict), fpin.read(), fpout)
        except ValueError as e:
            raise kt.ImageUploadError(
                "{filename} has invalid Exif data\n".format(filename=filename)
            ) from e

    # and upload the memory buffer
    headers = {"X-Auth-Token": kt.get_auth_token()}
    parameters = {
        "sequenceId": sequence_id,
        "sequenceIndex": geotags["sequence_index"],
        "coordinate": geotags["lat"] + "," + geotags["lon"],
        "projectionYaw": ((args.camera_yaw + 180) % 360) - 180,  # -180..180
        "shotDate": geotags["timestamp"],
        "payload": fpout.read(),
    }
    if "heading" in geotags:
        parameters["heading"] = geotags["heading"]
    if "accuracy" in geotags:
        parameters["gpsAccuracy"] = geotags["accuracy"]

    if args.dry_run:
        geotags["status_code"] = 666
        return geotags

    try:
        r = requests.post(
            PHOTO_ENDPOINT, headers=headers, data=parameters, timeout=API_TIMEOUT
        )
        geotags["status_code"] = r.status_code
        geotags["date_added"] = r["data"]["dateAdded"]
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise kt.ImageUploadError(
            "{filename} server error while uploading\n".format(filename=filename)
        ) from e

    return geotags
