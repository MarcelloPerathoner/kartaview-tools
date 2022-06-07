======================================
 KartaView Commandline Image Uploader
======================================


Commandline tools to upload discrete images to `KartaView <https://www.kartaview.org/>`_.

Also supports Vantrue OnDash X4S videos.


Usage
=====

0. Install:

   .. code-block:: console

      pip3 install kartaview-tools


1. Authorize:  This step is needed only once.  You need an OSM account.

   .. code-block:: console

      kv_auth

   Your KartaView credentials are now stored in the file
   :code:`~/.config/kartaview/credentials.json`.  Keep this file secret.  The KartaView credentials
   do not expire, but you can delete the credentials file, in which case you must authorize again.


2. Sequence the images:

   .. code-block:: console

      kv_sequence ~/Pictures/kartaview/*.jpg

   This step sorts your images into sequences. It extracts the GPS data from your images and stores
   it in sidecar files, where you can easily review it.


3. Upload the images:

   .. code-block:: console

      kv_upload ~/Pictures/kartaview/*.jpg

   The script remembers which images were successfully uploaded.  In case of errors, if you run the
   upload script on the same images again, the ones already uploaded will not be uploaded again.


Run the scripts with '-h' to see more options.


Videos
======

This software only supports videos produced by the Vantrue OnDas X4S dashcam.

To split a video file into discrete images use ffmpeg.

Proposed workflow:

.. code-block:: console

   mkdir -p /tmp/frames
   # extract I-frames for better clarity
   ffmpeg -skip_frame nointra -i ~/Videos/dash.mp4 -vsync 0 -frame_pts 1 /tmp/frames/%08d.jpg
   # patch GPS data into image files
   kv_vantrue_x4s -i ~/Videos/dash.mp4 /tmp/frames/%08d.jpg
   # sequence image files
   kv_sequence /tmp/frames/%08d.jpg
   # upload files
   kv_upload /tmp/frames/%08d.jpg

See:

- https://trac.ffmpeg.org/wiki/Create%20a%20thumbnail%20image%20every%20X%20seconds%20of%20the%20video


GPX Files
---------

You can extract the GPS data in your video into a GPX file and then use third-party
tools (eg.  exiftool) to further process it.

Proposed workflow:

.. code-block:: console

   kv_vantrue_x4s -i ~/Videos/dash.mp4 --gpx=track.gpx
   exiftool -geotag=track.gpx /tmp/frames

See: https://exiftool.org/geotag.html
