import os
import re
import cloudinary
import cloudinary.uploader

cloudinary.config(
    cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME', ''),
    api_key=os.environ.get('CLOUDINARY_API_KEY', ''),
    api_secret=os.environ.get('CLOUDINARY_API_SECRET', ''),
    secure=True
)


AUTO_OPTIMIZE = {'quality': 'auto', 'fetch_format': 'auto'}
MAX_UPLOAD = 5 * 1024 * 1024


def is_real_image(file):
    """按文件头魔数校验真实图片类型，客户端 mimetype 可伪造不可信。读后复位流。"""
    head = file.read(12)
    file.seek(0)
    if head[:3] == b'\xff\xd8\xff':                     # JPEG
        return True
    if head[:8] == b'\x89PNG\r\n\x1a\n':                # PNG
        return True
    if head[:6] in (b'GIF87a', b'GIF89a'):             # GIF
        return True
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':  # WebP
        return True
    return False


def upload_to_cloudinary(file_storage, folder='qi/misc', **kwargs):
    if not file_storage or not file_storage.filename:
        return None
    file_storage.seek(0, os.SEEK_END)
    if file_storage.tell() > MAX_UPLOAD:
        file_storage.seek(0)
        return None
    file_storage.seek(0)
    if not is_real_image(file_storage):
        return None
    trans = list(kwargs.pop('transformation', None) or [{}])
    trans[-1] = {**trans[-1], **AUTO_OPTIMIZE}
    try:
        result = cloudinary.uploader.upload(file_storage, folder=folder, transformation=trans, **kwargs)
        return result['secure_url']
    except Exception:
        return None


def public_id_from_url(url):
    if not url or 'res.cloudinary.com' not in url or '/upload/' not in url:
        return None
    parts = url.split('/upload/', 1)[1].split('/')
    if parts and re.fullmatch(r'v\d+', parts[0]):
        parts = parts[1:]
    pid = '/'.join(parts).rsplit('.', 1)[0]
    return pid or None


def destroy_urls(*urls):
    for url in urls:
        pid = public_id_from_url(url)
        if not pid:
            continue
        try:
            cloudinary.uploader.destroy(pid)
        except Exception:
            pass
