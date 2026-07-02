import os
import cloudinary
import cloudinary.uploader

cloudinary.config(
    cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME', ''),
    api_key=os.environ.get('CLOUDINARY_API_KEY', ''),
    api_secret=os.environ.get('CLOUDINARY_API_SECRET', ''),
    secure=True
)


def upload_to_cloudinary(file_storage, folder='qi/misc', **kwargs):
    if not file_storage or not file_storage.filename:
        return None
    try:
        result = cloudinary.uploader.upload(file_storage, folder=folder, **kwargs)
        return result['secure_url']
    except Exception:
        return None
