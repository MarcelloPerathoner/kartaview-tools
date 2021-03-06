"""The OpenStreetCam 1.0 API."""

import datetime
import hashlib
import json
import logging
import os
from typing import Dict

import requests

import kartaview_tools as kt
from .gpsfix import Geotags


API_ENDPOINT = "https://api.openstreetcam.org/"

SEQUENCE_ENDPOINT = API_ENDPOINT + "1.0/sequence/"
PHOTO_ENDPOINT = API_ENDPOINT + "1.0/photo/"

API_TIMEOUT = 120.0


def create_sequence(args, parameters: Dict[str, str]) -> int:
    """Create a new sequence on the server.

    typical_response = {
        "status": {
            "apiCode": 600,
            "apiMessage": "The request has been processed without incidents",
            "httpCode": 200,
            "httpMessage": "Success"
        },
        "osv": {
            "sequence": {
                "id": "1234567",
                "userId": "4269",
                "dateAdded": "2022-03-13 18:21:47",
                "currentLat": "0.000000",
                "currentLng": "0.000000",
                "countryCode": null,
                "stateCode": null,
                "status": "active",
                "imagesStatus": "NEW",
                "metaDataFilename": "/files/photo/2022/3/13/",
                "detectedSignsFilename": null,
                "clientTotal": null,
                "clientTotalDetails": null,
                "obdInfo": null,
                "platformName": null,
                "platformVersion": null,
                "appVersion": null,
                "track": null,
                "matchTrack": null,
                "reviewed": null,
                "changes": null,
                "recognitions": null,
                "address": null,
                "sequenceType": null,
                "uploadSource": "kartaview_tools 0.0.1",
                "distance": null,
                "processingStatus": "NEW",
                "countActivePhotos": "0"
            }
        }
    }
    """
    parameters.update(
        {
            "access_token": kt.get_auth_token(),
            "uploadSource": "kartaview_tools 0.0.1",
        }
    )

    if args.verbose:
        logging.debug("POST %s" % SEQUENCE_ENDPOINT)
        logging.debug("params: %s" % parameters)
    if args.dry_run:
        return 0

    try:
        r = requests.post(SEQUENCE_ENDPOINT, data=parameters, timeout=API_TIMEOUT)
        jso = r.json()
        if args.verbose:
            logging.debug(json.dumps(jso, ensure_ascii=False, indent=4))
        r.raise_for_status()
        return int(jso["osv"]["sequence"]["id"])
    except (requests.exceptions.RequestException, KeyError) as e:
        raise kt.SequenceCreationError(
            "Server error while creating a new sequence\n"
        ) from e


def close_sequence(args, sequence_id: int) -> None:
    """Close a sequence on the server.

    typical_response = {
        "status": {
            "apiCode" : 600,
            "apiMessage" : "The request has been processed without incidents",
            "httpCode" : 200,
            "httpMessage" : "Success"
        },
        "osv": {
            "sequenceId": "1234567"
        }
    }
    """
    parameters = {"access_token": kt.get_auth_token(), "sequenceId": sequence_id}
    url = SEQUENCE_ENDPOINT + "finished-uploading/"
    if args.verbose:
        logging.debug("POST %s" % url)
        logging.debug("params: %s" % parameters)
    if args.dry_run:
        return
    try:
        r = requests.post(url, data=parameters, timeout=API_TIMEOUT)
        if args.verbose:
            jso = r.json()
            logging.debug(json.dumps(jso, ensure_ascii=False, indent=4))
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise kt.SequenceClosingError(
            f"Server error while closing sequence {sequence_id}\n"
        ) from e


def upload_image(args, sequence_id: int, geotags: Geotags) -> Geotags:  # noqa: C901
    """Upload one image to the server.

    typical_response = {
        "status": {
            "apiCode": 600,
            "apiMessage": "The request has been processed without incidents",
            "httpCode": 200,
            "httpMessage": "Success"
        },
        "osv": {
            "photo": {
                "id": "1234567890",
                "sequenceId": "1234567",
                "dateAdded": "2022-03-04 05:06:07",
                "sequenceIndex": "1234",
                "photoName": "12345678.jpg",
                "lat": "30.999999",
                "lng": "15.888888",
                "gpsAccuracy": null,
                "headers": null,
                "autoImgProcessingResult": "ORIGINAL",
                "status": "active",
                "multipleInsert": [],
                "path": "storage13/files/photo/2022/3/4",
                "projection": "PLANE",
                "videoIndex": null,
                "visibility": "public"
            }
        }
    }
    """
    filename = geotags["filename"]

    fpout = kt.open_and_patch(filename, geotags)

    # and upload the memory buffer
    parameters = {
        "access_token": kt.get_auth_token(),
        "sequenceId": sequence_id,
        "sequenceIndex": geotags["sequence_index"],
        "coordinate": str(geotags["lat"]) + "," + str(geotags["lon"]),
        # according to osc_api_gateway.py expects UTC
        "shotDate": datetime.datetime.fromisoformat(geotags["timestamp"]).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    }

    def setparam(a, b):
        if v := geotags.get(b):
            parameters[a] = v

    setparam("projectionYaw", "projection_yaw")
    setparam("heading", "heading")
    setparam("headers", "heading")  # typo in API?
    setparam("direction", "direction")
    setparam("gpsAccuracy", "accuracy")

    name = str(
        hashlib.md5(
            str(parameters["coordinate"] + geotags["timestamp"]).encode()
        ).hexdigest()
    )
    extension = os.path.split(filename)[1]
    files = {"photo": (name + extension, fpout, "image/jpeg")}

    if args.verbose:
        logging.debug("POST %s" % PHOTO_ENDPOINT)
        logging.debug("params: %s" % parameters)
        logging.debug("files: %s" % files)

    if args.dry_run:
        geotags["status_code"] = 666
        return geotags

    try:
        r = requests.post(
            PHOTO_ENDPOINT, data=parameters, files=files, timeout=API_TIMEOUT
        )
        geotags["status_code"] = r.status_code
        jso = r.json()
        if args.verbose:
            logging.debug(json.dumps(jso, ensure_ascii=False, indent=4))
        r.raise_for_status()
        geotags["photo_id"] = jso["osv"]["photo"]["id"]
    except requests.exceptions.ReadTimeout as e:
        raise kt.ImageUploadError(
            f"Server timeout while uploading {filename} in sequence {sequence_id}"
        ) from e
    except requests.exceptions.JSONDecodeError as e:
        raise kt.ImageUploadError(
            f"Server returned bogus JSON while uploading {filename} in sequence {sequence_id}"
        ) from e
    except Exception as e:
        # catch (nearly) everything and transmogrify it into an ImageUploadError exception
        status = jso["status"]
        if status["apiCode"] == 660 and "duplicate" in status["apiMessage"]:
            # Do not complain about duplicates.  They may happen if the api timed out
            # and made us miss the answer, but the upload was accepted anyway.
            geotags["status_code"] = 200
            return geotags
        raise kt.ImageUploadError(
            f"Server error while uploading {filename} in sequence {sequence_id}:\n{jso['status']['apiMessage']}"
        ) from e

    return geotags
