"""The OpenStreetCam 2.0 API.  This is as yet *UNTESTED*."""

import json
import logging
from typing import Dict

import requests

import kartaview_tools as kt
from .gpsfix import Geotags


API_ENDPOINT = "https://api.openstreetcam.org/2.0/"

SEQUENCE_ENDPOINT = API_ENDPOINT + "sequence/"
PHOTO_ENDPOINT = API_ENDPOINT + "photo/"

API_TIMEOUT = 60.0


def create_sequence(args, parameters: Dict[str, str] = {}) -> int:
    """Create a new sequence on the server.

    typical_response = {
        "status": {
            "apiCode": 600,
            "apiMessage": "The request has been processed without incidents",
            "httpCode": 200,
            "httpMessage": "Success"
        },
        "result": {
            "data": {
                "id": "1234567",
                "userId": "4269",
                "address": null,
                "appVersion": null,
                "blurBuild": "0",
                "blurVersion": "v1",
                "cameraParameters": null,
                "clientTotal": null,
                "countActivePhotos": "0",
                "countMetadataPhotos": "0",
                "countMetadataVideos": "0",
                "countryCode": null,
                "currentLat": "0.000000",
                "currentLng": "0.000000",
                "dateAdded": "2022-06-07 20:21:22",
                "dateProcessed": null,
                "deviceName": "Vantrue OnDash X4S",
                "distance": null,
                "hasRawData": "0",
                "imageProcessingStatus": "NEW",
                "isVideo": "0",
                "matchStatus": "NEW",
                "matched": null,
                "metaDataFilename": null,
                "metadataStatus": "NEW",
                "nwLat": null,
                "nwLng": null,
                "obdInfo": null,
                "orgCode": "CMNT",
                "platformName": null,
                "platformVersion": null,
                "processingStatus": "NEW",
                "quality": null,
                "qualityStatus": "NEW",
                "seLat": null,
                "seLng": null,
                "sequenceType": null,
                "stateCode": null,
                "status": "active",
                "storage": null,
                "uploadSource": null,
                "uploadStatus": "UPLOADING"
            }
        }
    }
    """
    headers = {"X-Auth-Token": kt.get_auth_token()}
    if args.verbose:
        logging.info("POST %s" % SEQUENCE_ENDPOINT)
        logging.info("params: %s" % parameters)
    if args.dry_run:
        return 0

    try:
        r = requests.post(
            SEQUENCE_ENDPOINT, data=parameters, headers=headers, timeout=API_TIMEOUT
        )
        jso = r.json()
        if args.verbose:
            logging.info(json.dumps(jso, ensure_ascii=False, indent=4))
        r.raise_for_status()
        return int(jso["result"]["data"]["id"])
    except (requests.exceptions.RequestException, KeyError) as e:
        raise kt.SequenceCreationError(
            "Server error while creating a new sequence\n"
        ) from e


def close_sequence(args, sequence_id: int) -> None:
    """Close a sequence on the server."""
    headers = {"X-Auth-Token": kt.get_auth_token()}
    url = SEQUENCE_ENDPOINT + str(sequence_id) + "/finish"
    if args.verbose:
        logging.debug("GET %s" % url)
    if args.dry_run:
        return
    try:
        r = requests.get(url, headers=headers)
        if args.verbose:
            jso = r.json()
            logging.debug(json.dumps(jso, ensure_ascii=False, indent=4))
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise kt.SequenceClosingError(
            f"Server error while closing sequence {sequence_id}\n"
        ) from e


def upload_image(args, sequence_id: int, geotags: Geotags) -> Geotags:
    """Upload one image to the server."""
    filename = geotags["filename"]
    fpout = kt.open_and_patch(filename, geotags)

    headers = {"X-Auth-Token": kt.get_auth_token()}
    parameters = {
        "sequenceId": sequence_id,
        "sequenceIndex": geotags["sequence_index"],
        "coordinate": str(geotags["lat"]) + "," + str(geotags["lon"]),
        "shotDate": geotags["timestamp"],
    }

    def setparam(a, b):
        if v := geotags.get(a):
            parameters[b] = v

    setparam("projectionYaw", "projection_yaw")
    setparam("heading", "heading")
    setparam("gpsAccuracy", "accuracy")

    if args.verbose:
        logging.debug("POST %s" % PHOTO_ENDPOINT)
        logging.debug("params: %s" % parameters)

    parameters["payload"] = fpout.read()

    if args.dry_run:
        geotags["status_code"] = 666
        return geotags

    try:
        r = requests.post(
            PHOTO_ENDPOINT, headers=headers, data=parameters, timeout=API_TIMEOUT
        )
        geotags["status_code"] = r.status_code
        jso = r.json()
        if args.verbose:
            logging.debug(json.dumps(jso, ensure_ascii=False, indent=4))
        r.raise_for_status()
        data = jso["result"]["data"]
        geotags["photo_id"] = data["id"]
        geotags["date_added"] = data["dateAdded"]
    except (requests.exceptions.RequestException, KeyError) as e:
        raise kt.ImageUploadError(
            f"Server error while uploading {filename} in sequence {sequence_id}."
        ) from e

    return geotags
