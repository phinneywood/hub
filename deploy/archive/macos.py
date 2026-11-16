"""What the archive asks of macOS itself: pictures made smaller (ImageIO), the text and pages of PDFs (Core Graphics
and PDFKit), text read from a picture (Vision), other documents' text (textutil), previews (Quick Look), and iCloud
Drive files fetched to this Mac. Everything happens on this Mac; nothing here reaches a network service except
iCloud's own download of the owner's files.

PyObjC comes with osxphotos in the archive's own Python. It is imported only when needed, so the rest of the
program (and Hub's tests, which stand in for this module) runs without it.
"""
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

OCR_PIXELS = 2600           # a page or picture is read for text at about this size
TEXTUTIL = '/usr/bin/textutil'
QLMANAGE = '/usr/bin/qlmanage'
BRCTL = '/usr/bin/brctl'


def _pool():
    import objc
    return objc.autorelease_pool()


def _url(path):
    from Foundation import NSURL
    return NSURL.fileURLWithPath_(str(path))


def _jpeg(image, quality):
    """A CGImage as JPEG bytes, with no metadata of any kind."""
    import Quartz
    from Foundation import NSMutableData
    data = NSMutableData.data()
    out = Quartz.CGImageDestinationCreateWithData(data, 'public.jpeg', 1, None)
    Quartz.CGImageDestinationAddImage(out, image, {Quartz.kCGImageDestinationLossyCompressionQuality: quality})
    if not Quartz.CGImageDestinationFinalize(out):
        raise OSError('the picture could not be written')
    return bytes(data)


def _scaled(path, pixels):
    """A picture file at most `pixels` on its longer side, turned the way it is meant to be seen, or None."""
    import Quartz
    source = Quartz.CGImageSourceCreateWithURL(_url(path), None)
    if source is None or Quartz.CGImageSourceGetCount(source) < 1:
        return None
    return Quartz.CGImageSourceCreateThumbnailAtIndex(source, 0, {
        Quartz.kCGImageSourceCreateThumbnailFromImageAlways: True,
        Quartz.kCGImageSourceCreateThumbnailWithTransform: True,
        Quartz.kCGImageSourceShouldCacheImmediately: True,
        Quartz.kCGImageSourceThumbnailMaxPixelSize: int(pixels)})


def picture(path, pixels, quality=0.8):
    """(JPEG bytes, width, height) of a picture file made at most `pixels` on its longer side, or None."""
    import Quartz
    with _pool():
        image = _scaled(path, pixels)
        if image is None:
            return None
        return _jpeg(image, quality), Quartz.CGImageGetWidth(image), Quartz.CGImageGetHeight(image)


def pdf_pages(path):
    """The text of each page of a PDF, as a list; [] for a PDF that is locked or can't be opened."""
    import Quartz
    with _pool():
        document = Quartz.PDFDocument.alloc().initWithURL_(_url(path))
        if document is None or document.isLocked():
            return []
        return [str(document.pageAtIndex_(n).string() or '') for n in range(document.pageCount())]


def pdf_count(path):
    import Quartz
    with _pool():
        document = Quartz.CGPDFDocumentCreateWithURL(_url(path))
        return Quartz.CGPDFDocumentGetNumberOfPages(document) if document else 0


def _pdf_image(path, number, pixels):
    """One page of a PDF (counting from 1) drawn on white at most `pixels` on its longer side, or None."""
    import Quartz
    document = Quartz.CGPDFDocumentCreateWithURL(_url(path))
    if document is None or not 1 <= number <= Quartz.CGPDFDocumentGetNumberOfPages(document):
        return None
    page = Quartz.CGPDFDocumentGetPage(document, number)
    box = Quartz.CGPDFPageGetBoxRect(page, Quartz.kCGPDFCropBox)
    turned = Quartz.CGPDFPageGetRotationAngle(page) % 360
    width, height = (box.size.height, box.size.width) if turned in (90, 270) else (box.size.width, box.size.height)
    if width <= 0 or height <= 0:
        return None
    scale = pixels / max(width, height)
    w, h = max(1, round(width * scale)), max(1, round(height * scale))
    context = Quartz.CGBitmapContextCreate(None, w, h, 8, 0, Quartz.CGColorSpaceCreateDeviceRGB(),
                                           Quartz.kCGImageAlphaNoneSkipLast)
    Quartz.CGContextSetRGBFillColor(context, 1, 1, 1, 1)
    Quartz.CGContextFillRect(context, Quartz.CGRectMake(0, 0, w, h))
    Quartz.CGContextTranslateCTM(context, w / 2, h / 2)
    Quartz.CGContextRotateCTM(context, -math.radians(turned))
    Quartz.CGContextScaleCTM(context, scale, scale)
    Quartz.CGContextTranslateCTM(context, -(box.origin.x + box.size.width / 2), -(box.origin.y + box.size.height / 2))
    Quartz.CGContextDrawPDFPage(context, page)
    return Quartz.CGBitmapContextCreateImage(context)


def pdf_page(path, number, pixels, quality=0.8):
    """(JPEG bytes, width, height) of one page of a PDF, or None."""
    import Quartz
    with _pool():
        image = _pdf_image(path, number, pixels)
        if image is None:
            return None
        return _jpeg(image, quality), Quartz.CGImageGetWidth(image), Quartz.CGImageGetHeight(image)


def _read_text(image):
    """The text Vision reads in a CGImage, top to bottom, a line at a time."""
    import Vision
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)    # not {}: PyObjC refuses it
    handler.performRequests_error_([request], None)
    found = []
    for seen in request.results() or []:
        best = seen.topCandidates_(1)
        if best:
            box = seen.boundingBox()
            found.append((-round(box.origin.y, 2), box.origin.x, str(best[0].string())))
    return '\n'.join(words for _, _, words in sorted(found))


def ocr_picture(path):
    """The text in a picture file, or ''."""
    with _pool():
        image = _scaled(path, OCR_PIXELS)
        return _read_text(image) if image is not None else ''


def ocr_pdf_page(path, number):
    """The text on one page of a PDF that has no text of its own (a scan), read from its picture."""
    with _pool():
        image = _pdf_image(path, number, OCR_PIXELS)
        return _read_text(image) if image is not None else ''


def textutil(path, fmt=None, timeout=120):
    """A Word, RTF or OpenDocument file's text, through macOS's own converter. `fmt` names the format of a file
    whose name doesn't say it ("docx")."""
    named = ['-format', fmt] if fmt else []
    done = subprocess.run([TEXTUTIL, *named, '-convert', 'txt', '-stdout', '-encoding', 'UTF-8', str(path)],
                          capture_output=True, timeout=timeout)
    if done.returncode != 0:
        raise OSError('textutil could not read it')
    return done.stdout.decode('utf-8', 'replace')


def quicklook(path, pixels, timeout=60):
    """A preview of any document Quick Look can draw (Pages, Numbers, Keynote, Office), as (JPEG bytes, w, h)."""
    out = tempfile.mkdtemp(prefix='archive-preview-')
    try:
        subprocess.run([QLMANAGE, '-t', '-s', str(int(pixels)), '-o', out, str(path)], capture_output=True, timeout=timeout)
        made = [p for p in Path(out).iterdir() if p.is_file()]
        return picture(made[0], pixels) if made else None
    finally:
        shutil.rmtree(out, ignore_errors=True)


def video_frame(path, pixels, quality=0.8):
    """A frame from early in a video file, as (JPEG bytes, w, h), or None."""
    import AVFoundation
    import CoreMedia
    import Quartz
    with _pool():
        asset = AVFoundation.AVURLAsset.URLAssetWithURL_options_(_url(path), None)
        maker = AVFoundation.AVAssetImageGenerator.assetImageGeneratorWithAsset_(asset)
        maker.setAppliesPreferredTrackTransform_(True)
        maker.setMaximumSize_((pixels, pixels))
        image, _, _ = maker.copyCGImageAtTime_actualTime_error_(CoreMedia.CMTimeMakeWithSeconds(1.0, 600), None, None)
        if image is None:
            return None
        return _jpeg(image, quality), Quartz.CGImageGetWidth(image), Quartz.CGImageGetHeight(image)


def download(path):
    """Ask iCloud Drive to bring a file that is only in iCloud onto this Mac. Returns at once; the file arrives in
    the background. It never evicts, moves or changes anything."""
    try:
        from Foundation import NSFileManager
        done, _ = NSFileManager.defaultManager().startDownloadingUbiquitousItemAtURL_error_(_url(path), None)
        if done:
            return True
    except Exception:  # noqa: BLE001 - fall back to iCloud's own command
        pass
    if os.path.exists(BRCTL):
        return subprocess.run([BRCTL, 'download', str(path)], capture_output=True, timeout=60).returncode == 0
    return False
