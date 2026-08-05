#!/usr/bin/env python3
"""
SOMNUS Universal File Processors
Comprehensive processors for all file types with graceful degradation
"""

import asyncio
import logging
import mimetypes
import os
import subprocess
import tempfile
import time
import json
import struct
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Union, Set
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Optional dependencies with graceful degradation
try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    import aiofiles
    AIOFILES_AVAILABLE = True
except ImportError:
    AIOFILES_AVAILABLE = False

# Core dependencies (always available)
import sqlite3
import zipfile
import tarfile
import xml.etree.ElementTree as ET

# Optional dependencies with graceful degradation
try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False

try:
    from psd_tools import PSDImage
    PSD_TOOLS_AVAILABLE = True
except ImportError:
    PSD_TOOLS_AVAILABLE = False

try:
    import ezdxf
    DXF_AVAILABLE = True
except ImportError:
    DXF_AVAILABLE = False

try:
    import trimesh
    TRIMESH_AVAILABLE = True
except ImportError:
    TRIMESH_AVAILABLE = False

try:
    import h5py
    HDF5_AVAILABLE = True
except ImportError:
    HDF5_AVAILABLE = False

try:
    import scipy.io
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False

try:
    import pydicom
    DICOM_AVAILABLE = True
except ImportError:
    DICOM_AVAILABLE = False

try:
    import librosa
    LIBROSA_AVAILABLE = True
except ImportError:
    LIBROSA_AVAILABLE = False

try:
    import ffmpeg
    FFMPEG_AVAILABLE = True
except ImportError:
    FFMPEG_AVAILABLE = False

try:
    import hl7apy
    from hl7apy.core import Message as HL7Message
    from hl7apy.parser import parse_message
    HL7APY_AVAILABLE = True
except ImportError:
    HL7APY_AVAILABLE = False

try:
    from fhir.resources.patient import Patient as FHIRPatient
    from fhir.resources import construct_fhir_element
    FHIR_AVAILABLE = True
except ImportError:
    FHIR_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class ProcessingConfig:
    """Configuration for file processing behavior"""
    subprocess_timeout: int = 30          # seconds for external process calls
    capability_check_timeout: int = 5     # seconds for binary availability checks
    max_text_preview_bytes: int = 10_000  # bytes read for text preview
    allowed_base_dirs: List[str] = field(default_factory=list)  # path whitelist (empty = all allowed)


@dataclass
class ProcessingCapabilities:
    """Track available processing capabilities"""
    tesseract_ocr: bool = TESSERACT_AVAILABLE
    psd_processing: bool = PSD_TOOLS_AVAILABLE
    dxf_processing: bool = DXF_AVAILABLE
    cad_processing: bool = TRIMESH_AVAILABLE
    hdf5_processing: bool = HDF5_AVAILABLE
    matlab_processing: bool = SCIPY_AVAILABLE
    dicom_processing: bool = DICOM_AVAILABLE
    audio_processing: bool = LIBROSA_AVAILABLE
    video_processing: bool = FFMPEG_AVAILABLE
    healthcare: bool = field(init=False)  # HL7/FHIR/CDA support

    # System capabilities
    imagemagick_available: bool = field(init=False)
    inkscape_available: bool = field(init=False)
    blender_available: bool = field(init=False)

    # Config for tunable timeouts
    _config: ProcessingConfig = field(default_factory=ProcessingConfig, repr=False)

    def __post_init__(self):
        """Check system-level capabilities"""
        timeout = self._config.capability_check_timeout
        self.imagemagick_available = self._check_command("convert", timeout)
        self.inkscape_available = self._check_command("inkscape", timeout)
        self.blender_available = self._check_command("blender", timeout)
        self.healthcare = HL7APY_AVAILABLE or FHIR_AVAILABLE

    def _check_command(self, command: str, timeout: int = 5) -> bool:
        """Check if system command is available"""
        try:
            subprocess.run(
                [command, "--version"],
                capture_output=True,
                timeout=timeout
            )
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
            return False

    def get_summary(self) -> Dict[str, bool]:
        """Get capability summary"""
        return {
            'text_ocr': self.tesseract_ocr,
            'creative_files': self.psd_processing or self.imagemagick_available,
            'cad_files': self.dxf_processing or self.cad_processing,
            'scientific_data': self.hdf5_processing or self.matlab_processing,
            'medical_imaging': self.dicom_processing,
            'healthcare': self.healthcare,
            'audio_processing': self.audio_processing,
            'video_processing': self.video_processing,
            'vector_graphics': self.inkscape_available,
            '3d_processing': self.cad_processing or self.blender_available
        }


@dataclass
class ProcessingResult:
    """Standardized processing result"""
    success: bool
    extracted_text: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    preview_data: Optional[bytes] = None
    thumbnail_data: Optional[bytes] = None
    error_message: Optional[str] = None
    processing_time: float = 0.0
    processor_used: str = ""
    capabilities_used: List[str] = field(default_factory=list)
    
    @property
    def has_content(self) -> bool:
        """Check if meaningful content was extracted"""
        return bool(self.extracted_text.strip() or self.metadata or self.preview_data)


def _sanitize_path_for_subprocess(
    file_path: Path,
    allowed_base_dirs: Optional[List[str]] = None
) -> str:
    """Return a resolved, absolute path string safe for use as a subprocess argument.

    Raises ValueError if the resolved path escapes any configured allowed base
    directories.  Always returns a plain string (never shell-expanded).  Callers
    MUST pass this as a list element — never with shell=True.
    """
    resolved = file_path.resolve()
    if allowed_base_dirs:
        if not any(str(resolved).startswith(str(Path(d).resolve())) for d in allowed_base_dirs):
            raise ValueError(
                f"Path '{resolved}' is outside allowed directories: {allowed_base_dirs}"
            )
    return str(resolved)


class BaseFileProcessor(ABC):
    """Abstract base class for all file processors"""

    def __init__(
        self,
        capabilities: ProcessingCapabilities,
        executor: ThreadPoolExecutor,
        config: Optional[ProcessingConfig] = None,
    ):
        self.capabilities = capabilities
        self.executor = executor
        self.config = config or ProcessingConfig()
        self.processor_name = self.__class__.__name__
    
    @abstractmethod
    def can_process(self, file_path: Path, mime_type: str) -> bool:
        """Check if this processor can handle the file"""
        pass
    
    @abstractmethod
    async def process_file(self, file_path: Path, metadata: Dict[str, Any]) -> ProcessingResult:
        """Process file and return results"""
        pass
    
    def get_supported_extensions(self) -> Set[str]:
        """Get supported file extensions"""
        return set()
    
    def get_supported_mime_types(self) -> Set[str]:
        """Get supported MIME types"""
        return set()
    
    async def generate_thumbnail(self, file_path: Path, size: Tuple[int, int] = (200, 200)) -> Optional[bytes]:
        """Generate thumbnail for file"""
        return None
    
    async def extract_basic_metadata(self, file_path: Path) -> Dict[str, Any]:
        """Extract basic file metadata"""
        stat = file_path.stat()
        return {
            'file_size': stat.st_size,
            'created_time': datetime.fromtimestamp(stat.st_ctime, timezone.utc).isoformat(),
            'modified_time': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            'extension': file_path.suffix.lower(),
            'basename': file_path.stem
        }


class CreativeFileProcessor(BaseFileProcessor):
    """Processor for creative files (PSD, AI, Sketch, etc.)"""
    
    def get_supported_extensions(self) -> Set[str]:
        return {'.psd', '.ai', '.eps', '.indd', '.sketch', '.fig', '.xd', '.svg'}
    
    def can_process(self, file_path: Path, mime_type: str) -> bool:
        ext = file_path.suffix.lower()
        return ext in self.get_supported_extensions()
    
    async def process_file(self, file_path: Path, metadata: Dict[str, Any]) -> ProcessingResult:
        start_time = time.time()
        ext = file_path.suffix.lower()
        
        try:
            if ext == '.psd':
                return await self._process_psd(file_path)
            elif ext == '.svg':
                return await self._process_svg(file_path)
            elif ext in ['.ai', '.eps']:
                return await self._process_vector_file(file_path)
            else:
                return await self._process_generic_creative(file_path)
        
        except Exception as e:
            logger.error(f"Creative file processing failed: {e}")
            return ProcessingResult(
                success=False,
                error_message=str(e),
                processing_time=time.time() - start_time,
                processor_used=self.processor_name
            )
    
    async def _process_psd(self, file_path: Path) -> ProcessingResult:
        """Process Photoshop PSD files"""
        if not self.capabilities.psd_processing:
            return await self._fallback_creative_processing(file_path)
        
        loop = asyncio.get_event_loop()
        
        def _extract_psd():
            psd = PSDImage.open(str(file_path))
            
            # Extract text layers
            text_content = []
            layer_info = []
            
            for layer in psd:
                if hasattr(layer, 'text') and layer.text:
                    text_content.append(layer.text)
                
                layer_info.append({
                    'name': layer.name,
                    'visible': layer.visible,
                    'opacity': getattr(layer, 'opacity', 255),
                    'blend_mode': getattr(layer, 'blend_mode', 'normal')
                })
            
            # Generate composite image for thumbnail
            composite = psd.composite()
            
            metadata = {
                'width': psd.width,
                'height': psd.height,
                'color_mode': str(psd.color_mode),
                'layer_count': len(list(psd)),
                'layers': layer_info[:20],  # Limit to first 20 layers
                'has_text_layers': bool(text_content)
            }
            
            return {
                'text': '\n'.join(text_content),
                'metadata': metadata,
                'composite': composite
            }
        
        try:
            result = await loop.run_in_executor(self.executor, _extract_psd)
            
            # Generate thumbnail
            thumbnail = None
            if result['composite']:
                thumbnail = await self._image_to_thumbnail(result['composite'])
            
            return ProcessingResult(
                success=True,
                extracted_text=result['text'],
                metadata=result['metadata'],
                thumbnail_data=thumbnail,
                processor_used=self.processor_name,
                capabilities_used=['psd_processing']
            )
        
        except Exception as e:
            logger.error(f"PSD processing failed: {e}")
            return await self._fallback_creative_processing(file_path)
    
    async def _process_svg(self, file_path: Path) -> ProcessingResult:
        """Process SVG vector files"""
        try:
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                svg_content = await f.read()
            
            # Parse SVG
            root = ET.fromstring(svg_content)
            
            # Extract text elements
            text_elements = []
            for text_elem in root.iter():
                if text_elem.tag.endswith('text') and text_elem.text:
                    text_elements.append(text_elem.text.strip())
            
            # Extract metadata
            width = root.get('width', 'unknown')
            height = root.get('height', 'unknown')
            viewbox = root.get('viewBox', 'unknown')
            
            # Count elements
            element_counts = {}
            for elem in root.iter():
                tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
                element_counts[tag] = element_counts.get(tag, 0) + 1
            
            metadata = {
                'width': width,
                'height': height,
                'viewBox': viewbox,
                'element_counts': element_counts,
                'has_text': bool(text_elements),
                'file_size_bytes': len(svg_content.encode())
            }
            
            # Generate thumbnail using Inkscape if available
            thumbnail = None
            if self.capabilities.inkscape_available:
                thumbnail = await self._svg_to_thumbnail(file_path)
            
            return ProcessingResult(
                success=True,
                extracted_text='\n'.join(text_elements),
                metadata=metadata,
                thumbnail_data=thumbnail,
                processor_used=self.processor_name,
                capabilities_used=['svg_processing'] + (['inkscape'] if thumbnail else [])
            )
        
        except Exception as e:
            logger.error(f"SVG processing failed: {e}")
            return ProcessingResult(
                success=False,
                error_message=str(e),
                processor_used=self.processor_name
            )
    
    async def _process_vector_file(self, file_path: Path) -> ProcessingResult:
        """Process AI/EPS vector files"""
        if self.capabilities.imagemagick_available:
            return await self._imagemagick_processing(file_path)
        else:
            return await self._fallback_creative_processing(file_path)
    
    async def _process_generic_creative(self, file_path: Path) -> ProcessingResult:
        """Generic creative file processing"""
        metadata = await self.extract_basic_metadata(file_path)
        
        # Try to extract any embedded text
        text_content = ""
        if file_path.suffix.lower() in ['.sketch', '.fig', '.xd']:
            # These are typically ZIP-based formats
            text_content = await self._extract_from_zip_based(file_path)
        
        return ProcessingResult(
            success=True,
            extracted_text=text_content,
            metadata=metadata,
            processor_used=self.processor_name
        )
    
    async def _fallback_creative_processing(self, file_path: Path) -> ProcessingResult:
        """Fallback processing for creative files"""
        metadata = await self.extract_basic_metadata(file_path)
        ext = file_path.suffix.lower()
        
        # Mapping of unsupported formats to their descriptions
        format_descriptions = {
            '.sketch': 'Sketch design file (full extraction requires Sketch installation)',
            '.fig': 'Figma design file (full extraction requires Figma API)',
            '.xd': 'Adobe XD design file (full extraction requires Adobe XD)',
            '.indd': 'InDesign document (full extraction requires InDesign)'
        }
        
        description = format_descriptions.get(ext, f"Creative file ({ext})")
        metadata['processing_note'] = 'Limited processing - specialized tools not available'
        
        return ProcessingResult(
            success=True,
            extracted_text=description,
            metadata=metadata,
            processor_used=f"{self.processor_name}_fallback"
        )
    
    async def _extract_from_zip_based(self, file_path: Path) -> str:
        """Extract text from ZIP-based creative formats"""
        try:
            with zipfile.ZipFile(file_path, 'r') as zf:
                text_parts = []
                
                # Look for JSON/XML files that might contain text
                for filename in zf.namelist():
                    if filename.lower().endswith(('.json', '.xml', '.txt')):
                        try:
                            content = zf.read(filename).decode('utf-8', errors='ignore')
                            # Extract readable text (basic cleanup)
                            if filename.lower().endswith('.json'):
                                data = json.loads(content)
                                text_parts.extend(self._extract_text_from_json(data))
                            else:
                                text_parts.append(content[:1000])  # Limit size
                        except (json.JSONDecodeError, UnicodeDecodeError, KeyError) as e:
                            logger.debug(f"Failed to extract from {filename}: {e}")
                            continue
                
                return '\n'.join(text_parts)
        except (zipfile.BadZipFile, OSError, IOError) as e:
            logger.debug(f"Failed to extract from ZIP-based format: {e}")
            return ""
    
    def _extract_text_from_json(self, data: Any) -> List[str]:
        """Recursively extract text values from JSON"""
        texts = []
        
        if isinstance(data, dict):
            for value in data.values():
                texts.extend(self._extract_text_from_json(value))
        elif isinstance(data, list):
            for item in data:
                texts.extend(self._extract_text_from_json(item))
        elif isinstance(data, str) and len(data.strip()) > 2:
            texts.append(data.strip())
        
        return texts
    
    async def _image_to_thumbnail(self, image, size: Tuple[int, int] = (200, 200)) -> bytes:
        """Convert PIL image to thumbnail bytes"""
        try:
            if hasattr(image, 'thumbnail'):
                image.thumbnail(size, Image.Resampling.LANCZOS)
            
            # Convert to RGB if necessary
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            # Save to bytes
            import io
            buffer = io.BytesIO()
            image.save(buffer, format='JPEG', quality=85)
            return buffer.getvalue()
        except (OSError, IOError, ValueError, AttributeError) as e:
            logger.debug(f"Image to thumbnail conversion failed: {e}")
            return None
    
    async def _svg_to_thumbnail(self, file_path: Path, size: Tuple[int, int] = (200, 200)) -> Optional[bytes]:
        """Convert SVG to thumbnail using Inkscape"""
        try:
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                tmp_path = tmp.name

            safe_path = _sanitize_path_for_subprocess(
                file_path, self.config.allowed_base_dirs or None
            )

            # Use Inkscape to convert SVG to PNG — each element is a discrete arg (no shell=True)
            cmd = [
                'inkscape',
                '--export-type=png',
                f'--export-filename={tmp_path}',
                f'--export-width={size[0]}',
                f'--export-height={size[1]}',
                safe_path,
            ]

            result = subprocess.run(cmd, capture_output=True, timeout=self.config.subprocess_timeout)
            
            if result.returncode == 0 and Path(tmp_path).exists():
                async with aiofiles.open(tmp_path, 'rb') as f:
                    thumbnail_data = await f.read()
                
                # Cleanup
                Path(tmp_path).unlink()
                return thumbnail_data
        
        except Exception as e:
            logger.error(f"SVG thumbnail generation failed: {e}")
        
        return None
    
    async def _imagemagick_processing(self, file_path: Path) -> ProcessingResult:
        """Process files using ImageMagick"""
        try:
            safe_path = _sanitize_path_for_subprocess(
                file_path, self.config.allowed_base_dirs or None
            )
            info_cmd = ['identify', '-format', '%w %h %m %z', safe_path]
            result = subprocess.run(
                info_cmd, capture_output=True, text=True,
                timeout=self.config.subprocess_timeout
            )
            
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                width, height, format_name = parts[0], parts[1], parts[2]
                bit_depth = parts[3] if len(parts) > 3 else 'unknown'
                
                metadata = {
                    'width': int(width),
                    'height': int(height),
                    'format': format_name,
                    'bit_depth': bit_depth,
                    'imagemagick_processed': True
                }
                
                # Generate thumbnail
                thumbnail = await self._imagemagick_thumbnail(file_path)
                
                return ProcessingResult(
                    success=True,
                    extracted_text=f"Vector graphics file: {file_path.name}",
                    metadata=metadata,
                    thumbnail_data=thumbnail,
                    processor_used=self.processor_name,
                    capabilities_used=['imagemagick']
                )
        
        except Exception as e:
            logger.error(f"ImageMagick processing failed: {e}")
        
        return await self._fallback_creative_processing(file_path)
    
    async def _imagemagick_thumbnail(self, file_path: Path, size: Tuple[int, int] = (200, 200)) -> Optional[bytes]:
        """Generate thumbnail using ImageMagick"""
        try:
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                tmp_path = tmp.name

            safe_path = _sanitize_path_for_subprocess(
                file_path, self.config.allowed_base_dirs or None
            )

            cmd = [
                'convert',
                safe_path + '[0]',  # First page/layer — safe_path is already sanitized
                '-thumbnail', f'{size[0]}x{size[1]}',
                '-quality', '85',
                tmp_path,
            ]

            result = subprocess.run(cmd, capture_output=True, timeout=self.config.subprocess_timeout)
            
            if result.returncode == 0 and Path(tmp_path).exists():
                async with aiofiles.open(tmp_path, 'rb') as f:
                    thumbnail_data = await f.read()
                
                Path(tmp_path).unlink()
                return thumbnail_data
        
        except Exception as e:
            logger.error(f"ImageMagick thumbnail failed: {e}")
        
        return None


class CADFileProcessor(BaseFileProcessor):
    """Processor for CAD and 3D files"""
    
    def get_supported_extensions(self) -> Set[str]:
        return {'.dwg', '.dxf', '.step', '.stp', '.iges', '.igs', '.stl', '.obj', '.ply', '.blend', '.fbx', '.3ds'}
    
    def can_process(self, file_path: Path, mime_type: str) -> bool:
        ext = file_path.suffix.lower()
        return ext in self.get_supported_extensions()
    
    async def process_file(self, file_path: Path, metadata: Dict[str, Any]) -> ProcessingResult:
        start_time = time.time()
        ext = file_path.suffix.lower()
        
        try:
            if ext == '.dxf':
                return await self._process_dxf(file_path)
            elif ext == '.stl':
                return await self._process_stl(file_path)
            elif ext in ['.obj', '.ply']:
                return await self._process_mesh(file_path)
            elif ext == '.blend':
                return await self._process_blender(file_path)
            else:
                return await self._process_generic_cad(file_path)
        
        except Exception as e:
            logger.error(f"CAD file processing failed: {e}")
            return ProcessingResult(
                success=False,
                error_message=str(e),
                processing_time=time.time() - start_time,
                processor_used=self.processor_name
            )
    
    async def _process_dxf(self, file_path: Path) -> ProcessingResult:
        """Process DXF CAD files"""
        if not self.capabilities.dxf_processing:
            return await self._fallback_cad_processing(file_path)
        
        loop = asyncio.get_event_loop()
        
        def _extract_dxf():
            doc = ezdxf.readfile(str(file_path))
            
            # Extract text entities
            text_entities = []
            entity_counts = {}
            
            for entity in doc.modelspace():
                entity_type = entity.dxftype()
                entity_counts[entity_type] = entity_counts.get(entity_type, 0) + 1
                
                # Extract text
                if entity_type in ['TEXT', 'MTEXT']:
                    if hasattr(entity, 'dxf') and hasattr(entity.dxf, 'text'):
                        text_entities.append(entity.dxf.text)
            
            # Get drawing info
            header = doc.header
            drawing_units = header.get('$INSUNITS', 'unknown')
            
            # Extract layers
            layers = [layer.dxf.name for layer in doc.layers]
            
            metadata = {
                'dxf_version': doc.dxfversion,
                'entity_counts': entity_counts,
                'layer_count': len(layers),
                'layers': layers[:20],  # Limit to first 20
                'drawing_units': drawing_units,
                'has_text': bool(text_entities)
            }
            
            return {
                'text': '\n'.join(text_entities),
                'metadata': metadata
            }
        
        try:
            result = await loop.run_in_executor(self.executor, _extract_dxf)
            
            return ProcessingResult(
                success=True,
                extracted_text=result['text'],
                metadata=result['metadata'],
                processor_used=self.processor_name,
                capabilities_used=['dxf_processing']
            )
        
        except Exception as e:
            logger.error(f"DXF processing failed: {e}")
            return await self._fallback_cad_processing(file_path)
    
    async def _process_stl(self, file_path: Path) -> ProcessingResult:
        """Process STL 3D files"""
        if not self.capabilities.cad_processing:
            return await self._fallback_cad_processing(file_path)
        
        loop = asyncio.get_event_loop()
        
        def _extract_stl():
            mesh = trimesh.load(str(file_path))
            
            metadata = {
                'vertex_count': len(mesh.vertices),
                'face_count': len(mesh.faces),
                'volume': float(mesh.volume) if mesh.is_watertight else None,
                'surface_area': float(mesh.area),
                'is_watertight': mesh.is_watertight,
                'bounds': mesh.bounds.tolist(),
                'center_mass': mesh.center_mass.tolist()
            }
            
            return metadata
        
        try:
            metadata = await loop.run_in_executor(self.executor, _extract_stl)
            
            return ProcessingResult(
                success=True,
                extracted_text=f"3D model: {file_path.name}",
                metadata=metadata,
                processor_used=self.processor_name,
                capabilities_used=['trimesh']
            )
        
        except Exception as e:
            logger.error(f"STL processing failed: {e}")
            return await self._fallback_cad_processing(file_path)
    
    async def _process_mesh(self, file_path: Path) -> ProcessingResult:
        """Process OBJ/PLY mesh files"""
        if not self.capabilities.cad_processing:
            return await self._fallback_cad_processing(file_path)
        
        loop = asyncio.get_event_loop()
        
        def _extract_mesh():
            mesh = trimesh.load(str(file_path))
            
            # Extract material information if available
            materials = []
            if hasattr(mesh, 'materials') and mesh.materials:
                for material in mesh.materials:
                    materials.append({
                        'name': getattr(material, 'name', 'unnamed'),
                        'diffuse': getattr(material, 'diffuse', []).tolist() if hasattr(material, 'diffuse') else None
                    })
            
            metadata = {
                'vertex_count': len(mesh.vertices),
                'face_count': len(mesh.faces),
                'materials': materials,
                'has_textures': hasattr(mesh, 'visual') and hasattr(mesh.visual, 'material'),
                'bounds': mesh.bounds.tolist(),
                'scale': (mesh.bounds[1] - mesh.bounds[0]).tolist()
            }
            
            return metadata
        
        try:
            metadata = await loop.run_in_executor(self.executor, _extract_mesh)
            
            return ProcessingResult(
                success=True,
                extracted_text=f"3D mesh: {file_path.name}",
                metadata=metadata,
                processor_used=self.processor_name,
                capabilities_used=['trimesh']
            )
        
        except Exception as e:
            logger.error(f"Mesh processing failed: {e}")
            return await self._fallback_cad_processing(file_path)
    
    async def _process_blender(self, file_path: Path) -> ProcessingResult:
        """Process Blender files"""
        if not self.capabilities.blender_available:
            return await self._fallback_cad_processing(file_path)
        
        try:
            # Use Blender in background mode to extract info
            script = '''
import bpy
import json

# Get scene info
scene = bpy.context.scene
objects = bpy.data.objects

result = {
    "object_count": len(objects),
    "mesh_count": len([o for o in objects if o.type == "MESH"]),
    "light_count": len([o for o in objects if o.type == "LIGHT"]),
    "camera_count": len([o for o in objects if o.type == "CAMERA"]),
    "objects": [{"name": o.name, "type": o.type} for o in objects][:20]
}

print("BLENDER_RESULT:" + json.dumps(result))
'''
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as script_file:
                script_file.write(script)
                script_path = script_file.name
            
            safe_blend_path = _sanitize_path_for_subprocess(
                file_path, self.config.allowed_base_dirs or None
            )
            cmd = [
                'blender',
                safe_blend_path,
                '--background',
                '--python', script_path,
            ]

            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=self.config.subprocess_timeout * 2  # Blender needs more time
            )
            
            # Parse result
            metadata = {}
            for line in result.stdout.split('\n'):
                if line.startswith('BLENDER_RESULT:'):
                    try:
                        metadata = json.loads(line[15:])
                        break
                    except (json.JSONDecodeError, ValueError) as e:
                        logger.debug(f"Failed to parse Blender output: {e}")
                        continue
            
            # Cleanup
            Path(script_path).unlink()
            
            if metadata:
                return ProcessingResult(
                    success=True,
                    extracted_text=f"Blender file: {file_path.name}",
                    metadata=metadata,
                    processor_used=self.processor_name,
                    capabilities_used=['blender']
                )
        
        except Exception as e:
            logger.error(f"Blender processing failed: {e}")
        
        return await self._fallback_cad_processing(file_path)
    
    async def _process_generic_cad(self, file_path: Path) -> ProcessingResult:
        """Generic CAD file processing"""
        metadata = await self.extract_basic_metadata(file_path)
        metadata['cad_format'] = file_path.suffix.lower()[1:]
        
        # Try to read as binary and extract any embedded strings
        text_content = await self._extract_strings_from_binary(file_path)
        
        return ProcessingResult(
            success=True,
            extracted_text=text_content,
            metadata=metadata,
            processor_used=self.processor_name
        )
    
    async def _fallback_cad_processing(self, file_path: Path) -> ProcessingResult:
        """Fallback processing for CAD files"""
        metadata = await self.extract_basic_metadata(file_path)
        ext = file_path.suffix.lower()
        
        # Mapping of CAD formats to descriptions
        format_descriptions = {
            '.dwg': '2D/3D CAD drawing (requires ezdxf/libredwg for full extraction)',
            '.dxf': 'CAD data file (requires ezdxf for full extraction)',
            '.step': '3D CAD model (requires opencascade for full extraction)',
            '.stp': '3D CAD model (requires opencascade for full extraction)',
            '.iges': '3D CAD model (requires opencascade for full extraction)',
            '.igs': '3D CAD model (requires opencascade for full extraction)',
            '.stl': '3D mesh file (requires trimesh/numpy for full extraction)',
            '.obj': '3D mesh file (requires trimesh for full extraction)',
            '.ply': '3D mesh file (requires trimesh for full extraction)',
            '.blend': 'Blender scene (requires Blender for full extraction)'
        }
        
        description = format_descriptions.get(ext, f"CAD model ({ext[1:]}) - specialized libraries required")
        metadata['processing_note'] = 'Limited processing - CAD tools not available'
        metadata['cad_format'] = ext[1:]
        
        # Attempt to extract any embedded text
        text_content = await self._extract_strings_from_binary(file_path, min_length=6)
        if text_content:
            description += f"\n\nExtracted text content:\n{text_content[:500]}"
        
        return ProcessingResult(
            success=True,
            extracted_text=description,
            metadata=metadata,
            processor_used=f"{self.processor_name}_fallback"
        )
    
    async def _extract_strings_from_binary(self, file_path: Path, min_length: int = 4) -> str:
        """Extract readable strings from binary CAD files"""
        try:
            strings = []
            
            async with aiofiles.open(file_path, 'rb') as f:
                # Read in chunks to handle large files
                chunk_size = 1024 * 1024  # 1MB chunks
                current_string = b""
                
                while True:
                    chunk = await f.read(chunk_size)
                    if not chunk:
                        break
                    
                    for byte in chunk:
                        if 32 <= byte <= 126:  # Printable ASCII
                            current_string += bytes([byte])
                        else:
                            if len(current_string) >= min_length:
                                strings.append(current_string.decode('ascii', errors='ignore'))
                            current_string = b""
                    
                    # Limit extraction to avoid too much data
                    if len(strings) > 100:
                        break
            
            # Filter meaningful strings
            meaningful_strings = [
                s for s in strings 
                if len(s.strip()) >= min_length and 
                not s.isdigit() and 
                any(c.isalpha() for c in s)
            ]
            
            return '\n'.join(meaningful_strings[:50])  # Limit to 50 strings
        
        except Exception as e:
            logger.error(f"String extraction failed: {e}")
            return ""


class ScientificFileProcessor(BaseFileProcessor):
    """Processor for scientific data files"""
    
    def get_supported_extensions(self) -> Set[str]:
        return {'.mat', '.hdf5', '.h5', '.nc', '.fits', '.nii', '.dicom', '.dcm', '.sav', '.dta', '.rdata'}
    
    def can_process(self, file_path: Path, mime_type: str) -> bool:
        ext = file_path.suffix.lower()
        return ext in self.get_supported_extensions()
    
    async def process_file(self, file_path: Path, metadata: Dict[str, Any]) -> ProcessingResult:
        start_time = time.time()
        ext = file_path.suffix.lower()
        
        try:
            if ext == '.mat':
                return await self._process_matlab(file_path)
            elif ext in ['.hdf5', '.h5']:
                return await self._process_hdf5(file_path)
            elif ext in ['.dicom', '.dcm']:
                return await self._process_dicom(file_path)
            else:
                return await self._process_generic_scientific(file_path)
        
        except Exception as e:
            logger.error(f"Scientific file processing failed: {e}")
            return ProcessingResult(
                success=False,
                error_message=str(e),
                processing_time=time.time() - start_time,
                processor_used=self.processor_name
            )
    
    async def _process_matlab(self, file_path: Path) -> ProcessingResult:
        """Process MATLAB .mat files"""
        if not self.capabilities.matlab_processing:
            return await self._fallback_scientific_processing(file_path)
        
        loop = asyncio.get_event_loop()
        
        def _extract_matlab():
            mat_data = scipy.io.loadmat(str(file_path))
            
            # Extract metadata
            variables = {}
            total_arrays = 0
            
            for key, value in mat_data.items():
                if not key.startswith('__'):  # Skip metadata keys
                    if isinstance(value, np.ndarray):
                        variables[key] = {
                            'shape': value.shape,
                            'dtype': str(value.dtype),
                            'size': value.size
                        }
                        total_arrays += 1
                    else:
                        variables[key] = {
                            'type': type(value).__name__,
                            'value_preview': str(value)[:100]
                        }
            
            # Create description
            description_parts = [
                f"MATLAB file with {len(variables)} variables",
                f"Arrays: {total_arrays}",
                "Variables:"
            ]
            
            for name, info in list(variables.items())[:10]:  # Limit to 10
                if 'shape' in info:
                    description_parts.append(f"  {name}: {info['shape']} {info['dtype']}")
                else:
                    description_parts.append(f"  {name}: {info['type']}")
            
            metadata = {
                'variable_count': len(variables),
                'array_count': total_arrays,
                'variables': variables
            }
            
            return {
                'text': '\n'.join(description_parts),
                'metadata': metadata
            }
        
        try:
            result = await loop.run_in_executor(self.executor, _extract_matlab)
            
            return ProcessingResult(
                success=True,
                extracted_text=result['text'],
                metadata=result['metadata'],
                processor_used=self.processor_name,
                capabilities_used=['scipy']
            )
        
        except Exception as e:
            logger.error(f"MATLAB processing failed: {e}")
            return await self._fallback_scientific_processing(file_path)
    
    async def _process_hdf5(self, file_path: Path) -> ProcessingResult:
        """Process HDF5 scientific data files"""
        if not self.capabilities.hdf5_processing:
            return await self._fallback_scientific_processing(file_path)
        
        loop = asyncio.get_event_loop()
        
        def _extract_hdf5():
            with h5py.File(str(file_path), 'r') as f:
                
                def explore_group(group, prefix=""):
                    items = {}
                    for key in group.keys():
                        item = group[key]
                        item_path = f"{prefix}/{key}" if prefix else key
                        
                        if isinstance(item, h5py.Dataset):
                            items[item_path] = {
                                'type': 'dataset',
                                'shape': item.shape,
                                'dtype': str(item.dtype),
                                'size': item.size
                            }
                        elif isinstance(item, h5py.Group):
                            items[item_path] = {'type': 'group'}
                            # Recursively explore subgroups (limit depth)
                            if prefix.count('/') < 3:
                                items.update(explore_group(item, item_path))
                    
                    return items
                
                structure = explore_group(f)
                
                # Count datasets
                datasets = [k for k, v in structure.items() if v.get('type') == 'dataset']
                groups = [k for k, v in structure.items() if v.get('type') == 'group']
                
                # Create description
                description_parts = [
                    f"HDF5 file with {len(datasets)} datasets and {len(groups)} groups",
                    "Structure:"
                ]
                
                for path, info in list(structure.items())[:20]:  # Limit to 20
                    if info.get('type') == 'dataset':
                        description_parts.append(f"  {path}: {info['shape']} {info['dtype']}")
                    else:
                        description_parts.append(f"  {path}/")
                
                metadata = {
                    'dataset_count': len(datasets),
                    'group_count': len(groups),
                    'structure': structure
                }
                
                return {
                    'text': '\n'.join(description_parts),
                    'metadata': metadata
                }
        
        try:
            result = await loop.run_in_executor(self.executor, _extract_hdf5)
            
            return ProcessingResult(
                success=True,
                extracted_text=result['text'],
                metadata=result['metadata'],
                processor_used=self.processor_name,
                capabilities_used=['hdf5']
            )
        
        except Exception as e:
            logger.error(f"HDF5 processing failed: {e}")
            return await self._fallback_scientific_processing(file_path)
    
    async def _process_dicom(self, file_path: Path) -> ProcessingResult:
        """Process DICOM medical imaging files"""
        if not self.capabilities.dicom_processing:
            return await self._fallback_scientific_processing(file_path)
        
        loop = asyncio.get_event_loop()
        
        def _extract_dicom():
            ds = pydicom.dcmread(str(file_path))
            
            # Extract key metadata
            metadata = {}
            
            # Patient information (anonymized)
            if hasattr(ds, 'PatientID'):
                metadata['patient_id'] = 'ANONYMIZED'
            if hasattr(ds, 'PatientAge'):
                metadata['patient_age'] = str(ds.PatientAge)
            if hasattr(ds, 'PatientSex'):
                metadata['patient_sex'] = str(ds.PatientSex)
            
            # Study information
            if hasattr(ds, 'StudyDescription'):
                metadata['study_description'] = str(ds.StudyDescription)
            if hasattr(ds, 'Modality'):
                metadata['modality'] = str(ds.Modality)
            if hasattr(ds, 'StudyDate'):
                metadata['study_date'] = str(ds.StudyDate)
            
            # Image information
            if hasattr(ds, 'Rows') and hasattr(ds, 'Columns'):
                metadata['image_size'] = [int(ds.Rows), int(ds.Columns)]
            if hasattr(ds, 'PixelSpacing'):
                metadata['pixel_spacing'] = [float(x) for x in ds.PixelSpacing]
            if hasattr(ds, 'SliceThickness'):
                metadata['slice_thickness'] = float(ds.SliceThickness)
            
            # Create description
            description_parts = [
                f"DICOM medical image: {metadata.get('modality', 'unknown')}",
                f"Study: {metadata.get('study_description', 'unknown')}",
            ]
            
            if 'image_size' in metadata:
                description_parts.append(f"Image size: {metadata['image_size'][0]}x{metadata['image_size'][1]}")
            
            return {
                'text': '\n'.join(description_parts),
                'metadata': metadata
            }
        
        try:
            result = await loop.run_in_executor(self.executor, _extract_dicom)
            
            return ProcessingResult(
                success=True,
                extracted_text=result['text'],
                metadata=result['metadata'],
                processor_used=self.processor_name,
                capabilities_used=['pydicom']
            )
        
        except Exception as e:
            logger.error(f"DICOM processing failed: {e}")
            return await self._fallback_scientific_processing(file_path)
    
    async def _process_generic_scientific(self, file_path: Path) -> ProcessingResult:
        """Generic scientific file processing"""
        metadata = await self.extract_basic_metadata(file_path)
        metadata['scientific_format'] = file_path.suffix.lower()[1:]
        
        return ProcessingResult(
            success=True,
            extracted_text=f"Scientific data file: {file_path.name}",
            metadata=metadata,
            processor_used=self.processor_name
        )
    
    async def _fallback_scientific_processing(self, file_path: Path) -> ProcessingResult:
        """Fallback processing for scientific files"""
        metadata = await self.extract_basic_metadata(file_path)
        ext = file_path.suffix.lower()
        
        # Mapping of scientific formats to descriptions
        format_descriptions = {
            '.mat': 'MATLAB file (requires scipy/h5py for full extraction)',
            '.hdf5': 'HDF5 scientific data (requires h5py for full extraction)',
            '.h5': 'HDF5 scientific data (requires h5py for full extraction)',
            '.dcm': 'DICOM medical image (requires pydicom for full extraction)',
            '.dicom': 'DICOM medical image (requires pydicom for full extraction)',
            '.nii': 'NIFTI neuroimaging data (requires nibabel for full extraction)',
            '.nii.gz': 'NIFTI neuroimaging data (requires nibabel for full extraction)',
            '.fif': 'MEG/EEG data (requires mne-python for full extraction)',
            '.edf': 'EEG data (requires pyEDFlib for full extraction)'
        }
        
        description = format_descriptions.get(ext, f"Scientific data file ({ext[1:]}) - specialized libraries required")
        metadata['processing_note'] = 'Limited processing - scientific libraries not available'
        metadata['scientific_format'] = ext[1:]
        
        return ProcessingResult(
            success=True,
            extracted_text=description,
            metadata=metadata,
            processor_used=f"{self.processor_name}_fallback"
        )


class BlockchainFileProcessor(BaseFileProcessor):
    """Processor for blockchain and smart contract files"""
    
    def get_supported_extensions(self) -> Set[str]:
        return {'.sol', '.vyper', '.move', '.rs', '.go'}
    
    def can_process(self, file_path: Path, mime_type: str) -> bool:
        ext = file_path.suffix.lower()
        return ext in self.get_supported_extensions()
    
    async def process_file(self, file_path: Path, metadata: Dict[str, Any]) -> ProcessingResult:
        start_time = time.time()
        ext = file_path.suffix.lower()
        
        try:
            if ext == '.sol':
                return await self._process_solidity(file_path)
            elif ext == '.vyper':
                return await self._process_vyper(file_path)
            elif ext == '.move':
                return await self._process_move(file_path)
            else:
                return await self._process_generic_blockchain(file_path)
        
        except Exception as e:
            logger.error(f"Blockchain file processing failed: {e}")
            return ProcessingResult(
                success=False,
                error_message=str(e),
                processing_time=time.time() - start_time,
                processor_used=self.processor_name
            )
    
    async def _process_solidity(self, file_path: Path) -> ProcessingResult:
        """Process Solidity smart contract files"""
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
        
        # Extract contract information
        contracts = []
        functions = []
        events = []
        modifiers = []
        
        lines = content.split('\n')
        for line in lines:
            line = line.strip()
            
            # Extract contracts
            if line.startswith('contract ') or line.startswith('interface ') or line.startswith('library '):
                contracts.append(line.split()[1].split('{')[0])
            
            # Extract functions
            elif 'function ' in line and not line.startswith('//'):
                func_match = line.split('function ')[1].split('(')[0]
                functions.append(func_match)
            
            # Extract events
            elif line.startswith('event '):
                event_match = line.split('event ')[1].split('(')[0]
                events.append(event_match)
            
            # Extract modifiers
            elif line.startswith('modifier '):
                mod_match = line.split('modifier ')[1].split('(')[0]
                modifiers.append(mod_match)
        
        # Check for common patterns
        has_payable = 'payable' in content
        has_view = 'view' in content
        has_pure = 'pure' in content
        has_constructor = 'constructor' in content
        
        metadata = {
            'language': 'solidity',
            'contracts': contracts,
            'function_count': len(functions),
            'functions': functions[:20],  # Limit to 20
            'event_count': len(events),
            'events': events[:10],
            'modifier_count': len(modifiers),
            'modifiers': modifiers[:10],
            'has_payable_functions': has_payable,
            'has_view_functions': has_view,
            'has_pure_functions': has_pure,
            'has_constructor': has_constructor,
            'line_count': len(lines)
        }
        
        # Create description
        description_parts = [
            f"Solidity smart contract: {file_path.name}",
            f"Contracts: {', '.join(contracts) if contracts else 'none'}",
            f"Functions: {len(functions)}",
            f"Events: {len(events)}",
            f"Modifiers: {len(modifiers)}"
        ]
        
        return ProcessingResult(
            success=True,
            extracted_text='\n'.join(description_parts) + '\n\n' + content,
            metadata=metadata,
            processor_used=self.processor_name
        )
    
    async def _process_vyper(self, file_path: Path) -> ProcessingResult:
        """Process Vyper smart contract files"""
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
        
        # Extract Vyper-specific information
        functions = []
        events = []
        
        lines = content.split('\n')
        for line in lines:
            line = line.strip()
            
            # Extract functions (Vyper uses @external, @internal decorators)
            if line.startswith('def '):
                func_name = line.split('def ')[1].split('(')[0]
                functions.append(func_name)
            
            # Extract events
            elif line.startswith('event '):
                event_name = line.split('event ')[1].split(':')[0]
                events.append(event_name)
        
        metadata = {
            'language': 'vyper',
            'function_count': len(functions),
            'functions': functions[:20],
            'event_count': len(events),
            'events': events[:10],
            'line_count': len(lines)
        }
        
        description_parts = [
            f"Vyper smart contract: {file_path.name}",
            f"Functions: {len(functions)}",
            f"Events: {len(events)}"
        ]
        
        return ProcessingResult(
            success=True,
            extracted_text='\n'.join(description_parts) + '\n\n' + content,
            metadata=metadata,
            processor_used=self.processor_name
        )
    
    async def _process_move(self, file_path: Path) -> ProcessingResult:
        """Process Move language files"""
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
        
        # Extract Move-specific information
        modules = []
        functions = []
        structs = []
        
        lines = content.split('\n')
        for line in lines:
            line = line.strip()
            
            if line.startswith('module '):
                module_name = line.split('module ')[1].split(' ')[0]
                modules.append(module_name)
            elif line.startswith('public fun ') or line.startswith('fun '):
                func_name = line.split('fun ')[1].split('(')[0]
                functions.append(func_name)
            elif line.startswith('struct '):
                struct_name = line.split('struct ')[1].split(' ')[0]
                structs.append(struct_name)
        
        metadata = {
            'language': 'move',
            'modules': modules,
            'function_count': len(functions),
            'functions': functions[:20],
            'struct_count': len(structs),
            'structs': structs[:10],
            'line_count': len(lines)
        }
        
        description_parts = [
            f"Move language file: {file_path.name}",
            f"Modules: {', '.join(modules) if modules else 'none'}",
            f"Functions: {len(functions)}",
            f"Structs: {len(structs)}"
        ]
        
        return ProcessingResult(
            success=True,
            extracted_text='\n'.join(description_parts) + '\n\n' + content,
            metadata=metadata,
            processor_used=self.processor_name
        )
    
    async def _process_generic_blockchain(self, file_path: Path) -> ProcessingResult:
        """Generic blockchain file processing"""
        async with aiofiles.open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = await f.read()
        
        metadata = await self.extract_basic_metadata(file_path)
        metadata['blockchain_language'] = file_path.suffix.lower()[1:]
        metadata['line_count'] = len(content.split('\n'))
        
        return ProcessingResult(
            success=True,
            extracted_text=content,
            metadata=metadata,
            processor_used=self.processor_name
        )


class MediaFileProcessor(BaseFileProcessor):
    """Processor for advanced audio/video files"""
    
    def get_supported_extensions(self) -> Set[str]:
        return {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac'}
    
    def can_process(self, file_path: Path, mime_type: str) -> bool:
        ext = file_path.suffix.lower()
        return ext in self.get_supported_extensions() or mime_type.startswith(('audio/', 'video/'))
    
    async def process_file(self, file_path: Path, metadata: Dict[str, Any]) -> ProcessingResult:
        start_time = time.time()
        ext = file_path.suffix.lower()
        
        try:
            if ext in ['.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac']:
                return await self._process_audio(file_path)
            else:
                return await self._process_video(file_path)
        
        except Exception as e:
            logger.error(f"Media file processing failed: {e}")
            return ProcessingResult(
                success=False,
                error_message=str(e),
                processing_time=time.time() - start_time,
                processor_used=self.processor_name
            )
    
    async def _process_audio(self, file_path: Path) -> ProcessingResult:
        """Process audio files"""
        metadata = await self.extract_basic_metadata(file_path)
        
        if self.capabilities.audio_processing:
            try:
                loop = asyncio.get_event_loop()
                
                def _extract_audio():
                    # Load audio file
                    y, sr = librosa.load(str(file_path), duration=30)  # Load first 30 seconds
                    
                    # Extract features
                    duration = len(y) / sr
                    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
                    spectral_centroids = librosa.feature.spectral_centroid(y=y, sr=sr)
                    zero_crossing_rate = librosa.feature.zero_crossing_rate(y)
                    
                    return {
                        'duration_seconds': float(duration),
                        'sample_rate': int(sr),
                        'tempo_bpm': float(tempo),
                        'spectral_centroid_mean': float(np.mean(spectral_centroids)),
                        'zero_crossing_rate_mean': float(np.mean(zero_crossing_rate)),
                        'channels': 1 if len(y.shape) == 1 else y.shape[0]
                    }
                
                audio_metadata = await loop.run_in_executor(self.executor, _extract_audio)
                metadata.update(audio_metadata)
                
                description = f"Audio file: {file_path.name}\n"
                description += f"Duration: {audio_metadata['duration_seconds']:.2f} seconds\n"
                description += f"Sample rate: {audio_metadata['sample_rate']} Hz\n"
                description += f"Tempo: {audio_metadata['tempo_bpm']:.1f} BPM"
                
                return ProcessingResult(
                    success=True,
                    extracted_text=description,
                    metadata=metadata,
                    processor_used=self.processor_name,
                    capabilities_used=['librosa']
                )
            
            except Exception as e:
                logger.error(f"Audio analysis failed: {e}")
        
        # Fallback processing
        return ProcessingResult(
            success=True,
            extracted_text=f"Audio file: {file_path.name}",
            metadata=metadata,
            processor_used=self.processor_name
        )
    
    async def _process_video(self, file_path: Path) -> ProcessingResult:
        """Process video files"""
        metadata = await self.extract_basic_metadata(file_path)
        
        if self.capabilities.video_processing:
            try:
                # Use ffprobe to get video information
                safe_video_path = _sanitize_path_for_subprocess(
                    file_path, self.config.allowed_base_dirs or None
                )
                cmd = [
                    'ffprobe',
                    '-v', 'quiet',
                    '-print_format', 'json',
                    '-show_format',
                    '-show_streams',
                    safe_video_path,
                ]

                result = subprocess.run(
                    cmd, capture_output=True, text=True,
                    timeout=self.config.subprocess_timeout
                )
                
                if result.returncode == 0:
                    probe_data = json.loads(result.stdout)
                    
                    # Extract video metadata
                    format_info = probe_data.get('format', {})
                    streams = probe_data.get('streams', [])
                    
                    video_streams = [s for s in streams if s.get('codec_type') == 'video']
                    audio_streams = [s for s in streams if s.get('codec_type') == 'audio']
                    
                    video_metadata = {
                        'duration_seconds': float(format_info.get('duration', 0)),
                        'format_name': format_info.get('format_name', 'unknown'),
                        'bit_rate': int(format_info.get('bit_rate', 0)),
                        'video_streams': len(video_streams),
                        'audio_streams': len(audio_streams)
                    }
                    
                    if video_streams:
                        video_stream = video_streams[0]
                        video_metadata.update({
                            'width': int(video_stream.get('width', 0)),
                            'height': int(video_stream.get('height', 0)),
                            'codec': video_stream.get('codec_name', 'unknown'),
                            'pixel_format': video_stream.get('pix_fmt', 'unknown'),
                            'frame_rate': video_stream.get('r_frame_rate', 'unknown')
                        })
                    
                    metadata.update(video_metadata)
                    
                    description = f"Video file: {file_path.name}\n"
                    description += f"Duration: {video_metadata['duration_seconds']:.2f} seconds\n"
                    description += f"Format: {video_metadata['format_name']}\n"
                    if video_streams:
                        description += f"Resolution: {video_metadata.get('width', 0)}x{video_metadata.get('height', 0)}\n"
                        description += f"Codec: {video_metadata.get('codec', 'unknown')}"
                    
                    return ProcessingResult(
                        success=True,
                        extracted_text=description,
                        metadata=metadata,
                        processor_used=self.processor_name,
                        capabilities_used=['ffmpeg']
                    )
            
            except Exception as e:
                logger.error(f"Video analysis failed: {e}")
        
        # Fallback processing
        return ProcessingResult(
            success=True,
            extracted_text=f"Video file: {file_path.name}",
            metadata=metadata,
            processor_used=self.processor_name
        )


class HealthcareFileProcessor(BaseFileProcessor):
    """Processor for healthcare interoperability file formats.

    Supported formats:
        HL7 v2.x  — pipe-delimited messages (.hl7, .hl7v2)
        HL7 v3.x  — XML-encoded messages (.hl7v3)
        FHIR R4   — JSON or XML resources (.fhir, .fhir.json, .fhir.xml)
        CDA       — Clinical Document Architecture XML (.cda, .cda.xml)

    Gracefully degrades when hl7apy / fhir.resources are not installed.
    PHI is never stored verbatim; patient identifiers are anonymized.
    """

    # HL7 v2 segment types that carry patient demographics
    _HL7V2_PATIENT_SEGMENTS = frozenset({"PID", "PD1", "NK1"})
    # FHIR resource types we care about for metadata extraction
    _FHIR_RESOURCE_TYPES = frozenset({
        "Patient", "Observation", "DiagnosticReport", "Encounter",
        "Condition", "Procedure", "MedicationRequest", "AllergyIntolerance",
        "Bundle", "Composition",
    })

    def get_supported_extensions(self) -> Set[str]:
        return {
            '.hl7', '.hl7v2', '.hl7v3',
            '.fhir',
            '.cda',
        }

    def can_process(self, file_path: Path, mime_type: str) -> bool:
        ext = file_path.suffix.lower()
        if ext in self.get_supported_extensions():
            return True
        # Handle compound extensions like .fhir.json / .fhir.xml / .cda.xml
        compound = ''.join(file_path.suffixes[-2:]).lower()
        if compound in {'.fhir.json', '.fhir.xml', '.cda.xml'}:
            return True
        # MIME type matching
        healthcare_mimes = {
            'application/hl7-v2',
            'application/fhir+json',
            'application/fhir+xml',
            'text/xml+cda',
        }
        return mime_type in healthcare_mimes

    async def process_file(self, file_path: Path, metadata: Dict[str, Any]) -> ProcessingResult:
        start_time = time.time()
        ext = file_path.suffix.lower()
        compound = ''.join(file_path.suffixes[-2:]).lower()

        try:
            if ext in {'.hl7', '.hl7v2'}:
                result = await self._process_hl7v2(file_path)
            elif ext == '.hl7v3':
                result = await self._process_hl7v3_xml(file_path)
            elif ext == '.fhir' or compound in {'.fhir.json', '.fhir.xml'}:
                result = await self._process_fhir(file_path)
            elif ext == '.cda' or compound == '.cda.xml':
                result = await self._process_cda(file_path)
            else:
                # Try to infer from content
                result = await self._process_by_content_inspection(file_path)

            result.processing_time = time.time() - start_time
            return result

        except Exception as exc:
            logger.error("HealthcareFileProcessor: unhandled exception for %s: %s", file_path.name, exc)
            return ProcessingResult(
                success=False,
                error_message=f"Healthcare processing failed: {exc}",
                processing_time=time.time() - start_time,
                processor_used=self.processor_name,
            )

    # ── HL7 v2.x ──────────────────────────────────────────────────────────────

    async def _process_hl7v2(self, file_path: Path) -> ProcessingResult:
        """Parse HL7 v2.x pipe-delimited messages."""
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(
            self.executor,
            lambda: file_path.read_bytes()
        )
        # HL7 v2 files can use \r or \r\n as segment terminators
        text = raw.decode('utf-8', errors='replace').replace('\r\n', '\r').replace('\n', '\r')

        if HL7APY_AVAILABLE:
            return await self._parse_hl7v2_with_hl7apy(text, file_path)
        else:
            return await self._parse_hl7v2_fallback(text, file_path)

    async def _parse_hl7v2_with_hl7apy(self, text: str, file_path: Path) -> ProcessingResult:
        """Full HL7 v2 parsing via hl7apy."""
        loop = asyncio.get_event_loop()

        def _parse():
            try:
                msg = parse_message(text, find_groups=False)
            except Exception as parse_exc:
                logger.warning("hl7apy parse failed for %s: %s — falling back", file_path.name, parse_exc)
                return None

            segments: Dict[str, Any] = {}
            for segment in msg.children:
                seg_name = segment.name.upper()
                fields = [
                    str(f.value) if hasattr(f, 'value') else ''
                    for f in segment.children
                ]
                segments.setdefault(seg_name, []).append(fields)

            msh = segments.get('MSH', [[]])[0]
            meta: Dict[str, Any] = {
                'hl7_version': '2.x',
                'sending_application': msh[2] if len(msh) > 2 else 'unknown',
                'sending_facility': msh[3] if len(msh) > 3 else 'unknown',
                'receiving_application': msh[4] if len(msh) > 4 else 'unknown',
                'message_type': msh[8] if len(msh) > 8 else 'unknown',
                'message_control_id': msh[9] if len(msh) > 9 else 'unknown',
                'processing_id': msh[10] if len(msh) > 10 else 'unknown',
                'segment_types': sorted(set(segments.keys())),
                'segment_count': sum(len(v) for v in segments.values()),
                'has_patient_segment': 'PID' in segments,
                'patient_demographics': 'ANONYMIZED' if 'PID' in segments else None,
                'parser': 'hl7apy',
            }

            description = (
                f"HL7 v2 message — type: {meta['message_type']}\n"
                f"From: {meta['sending_application']} @ {meta['sending_facility']}\n"
                f"Segments ({meta['segment_count']}): {', '.join(meta['segment_types'])}"
            )
            return meta, description

        result = await loop.run_in_executor(self.executor, _parse)
        if result is None:
            return await self._parse_hl7v2_fallback(text, file_path)

        meta, description = result
        return ProcessingResult(
            success=True,
            extracted_text=description,
            metadata=meta,
            processor_used=self.processor_name,
            capabilities_used=['hl7apy'],
        )

    async def _parse_hl7v2_fallback(self, text: str, file_path: Path) -> ProcessingResult:
        """Regex-based HL7 v2 extraction when hl7apy is unavailable."""
        segments = [line.strip() for line in text.split('\r') if line.strip()]
        segment_types: Dict[str, int] = {}
        msh_fields: List[str] = []

        for seg in segments:
            seg_type = seg[:3].upper()
            segment_types[seg_type] = segment_types.get(seg_type, 0) + 1
            if seg_type == 'MSH' and not msh_fields:
                msh_fields = seg.split('|')

        msg_type = msh_fields[8] if len(msh_fields) > 8 else 'unknown'
        sending_app = msh_fields[2] if len(msh_fields) > 2 else 'unknown'
        sending_fac = msh_fields[3] if len(msh_fields) > 3 else 'unknown'

        meta: Dict[str, Any] = {
            'hl7_version': '2.x',
            'message_type': msg_type,
            'sending_application': sending_app,
            'sending_facility': sending_fac,
            'segment_types': sorted(segment_types.keys()),
            'segment_counts': segment_types,
            'total_segments': len(segments),
            'has_patient_segment': 'PID' in segment_types,
            'patient_demographics': 'ANONYMIZED' if 'PID' in segment_types else None,
            'parser': 'fallback',
        }
        description = (
            f"HL7 v2 message — type: {msg_type}\n"
            f"From: {sending_app} @ {sending_fac}\n"
            f"Segments ({len(segments)}): {', '.join(sorted(segment_types.keys()))}\n"
            f"Note: hl7apy not installed — limited parsing"
        )
        return ProcessingResult(
            success=True,
            extracted_text=description,
            metadata=meta,
            processor_used=f"{self.processor_name}_fallback",
            capabilities_used=[],
        )

    # ── HL7 v3 XML ────────────────────────────────────────────────────────────

    async def _process_hl7v3_xml(self, file_path: Path) -> ProcessingResult:
        """Parse HL7 v3 XML messages using stdlib xml.etree.ElementTree."""
        loop = asyncio.get_event_loop()

        def _parse():
            try:
                tree = ET.parse(str(file_path))
                root = tree.getroot()
            except ET.ParseError as exc:
                return None, str(exc)

            # Strip namespace for easier attribute access
            def _local(tag: str) -> str:
                return tag.split('}')[-1] if '}' in tag else tag

            root_tag = _local(root.tag)
            interaction_id = ''
            id_elem = root.find('.//{*}id')
            if id_elem is not None:
                interaction_id = id_elem.get('extension', id_elem.get('root', ''))

            # Count element types
            elem_types: Dict[str, int] = {}
            for elem in root.iter():
                tag = _local(elem.tag)
                elem_types[tag] = elem_types.get(tag, 0) + 1

            meta: Dict[str, Any] = {
                'hl7_version': '3.x',
                'root_element': root_tag,
                'interaction_id': interaction_id or 'unknown',
                'namespace': root.tag.split('}')[0].lstrip('{') if '}' in root.tag else '',
                'element_types': dict(sorted(elem_types.items(), key=lambda x: -x[1])[:20]),
                'total_elements': sum(elem_types.values()),
                'has_patient_data': any(
                    t in elem_types for t in ('patient', 'patientRole', 'Patient')
                ),
                'patient_demographics': 'ANONYMIZED' if any(
                    t in elem_types for t in ('patient', 'patientRole', 'Patient')
                ) else None,
                'parser': 'stdlib_xml',
            }
            description = (
                f"HL7 v3 XML message — root: {root_tag}\n"
                f"Interaction ID: {meta['interaction_id']}\n"
                f"Total elements: {meta['total_elements']}\n"
                f"Has patient data: {meta['has_patient_data']}"
            )
            return meta, description

        result = await loop.run_in_executor(self.executor, _parse)
        meta, description = result
        if meta is None:
            return ProcessingResult(
                success=False,
                error_message=f"HL7 v3 XML parse error: {description}",
                processor_used=self.processor_name,
            )
        return ProcessingResult(
            success=True,
            extracted_text=description,
            metadata=meta,
            processor_used=self.processor_name,
            capabilities_used=['stdlib_xml'],
        )

    # ── FHIR R4 ───────────────────────────────────────────────────────────────

    async def _process_fhir(self, file_path: Path) -> ProcessingResult:
        """Parse FHIR R4 resources — JSON or XML."""
        compound = ''.join(file_path.suffixes[-2:]).lower()
        is_xml = file_path.suffix.lower() in {'.xml'} or compound == '.fhir.xml'

        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(self.executor, lambda: file_path.read_bytes())
        text = raw.decode('utf-8', errors='replace')

        if is_xml:
            return await self._parse_fhir_xml(text, file_path)

        # JSON path
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            return ProcessingResult(
                success=False,
                error_message=f"FHIR JSON parse error: {exc}",
                processor_used=self.processor_name,
            )
        return await self._parse_fhir_json(parsed, file_path)

    async def _parse_fhir_json(self, parsed: Any, file_path: Path) -> ProcessingResult:
        """Extract FHIR R4 JSON resource metadata."""
        if not isinstance(parsed, dict):
            return ProcessingResult(
                success=False,
                error_message="FHIR JSON root must be a JSON object",
                processor_used=self.processor_name,
            )

        resource_type = parsed.get('resourceType', 'unknown')
        fhir_id = parsed.get('id', 'unknown')
        status = parsed.get('status', None)

        # Bundle handling
        entry_count = 0
        resource_types_in_bundle: List[str] = []
        if resource_type == 'Bundle':
            entries = parsed.get('entry', [])
            entry_count = len(entries)
            resource_types_in_bundle = list({
                e.get('resource', {}).get('resourceType', 'unknown')
                for e in entries
                if isinstance(e, dict)
            })

        # Anonymize patient data
        patient_anonymized = False
        if resource_type == 'Patient':
            patient_anonymized = True
            # Replace name/identifier/telecom fields in description only
            # (we never store the raw values)

        capabilities_used = ['fhir.resources'] if FHIR_AVAILABLE else []

        meta: Dict[str, Any] = {
            'fhir_version': 'R4',
            'resource_type': resource_type,
            'resource_id': fhir_id,
            'status': status,
            'entry_count': entry_count if resource_type == 'Bundle' else None,
            'bundle_resource_types': resource_types_in_bundle or None,
            'patient_demographics': 'ANONYMIZED' if patient_anonymized else None,
            'parser': 'json_stdlib',
        }
        description_lines = [
            f"FHIR R4 resource — type: {resource_type}",
            f"ID: {fhir_id}",
        ]
        if status:
            description_lines.append(f"Status: {status}")
        if resource_type == 'Bundle':
            description_lines.append(f"Bundle entries: {entry_count}")
            description_lines.append(f"Resource types: {', '.join(sorted(resource_types_in_bundle))}")

        return ProcessingResult(
            success=True,
            extracted_text='\n'.join(description_lines),
            metadata=meta,
            processor_used=self.processor_name,
            capabilities_used=capabilities_used,
        )

    async def _parse_fhir_xml(self, text: str, file_path: Path) -> ProcessingResult:
        """Extract FHIR R4 XML resource metadata."""
        loop = asyncio.get_event_loop()

        def _parse():
            try:
                root = ET.fromstring(text)
            except ET.ParseError as exc:
                return None, str(exc)

            def _local(tag: str) -> str:
                return tag.split('}')[-1] if '}' in tag else tag

            resource_type = _local(root.tag)
            fhir_id_elem = root.find('.//{*}id')
            fhir_id = fhir_id_elem.get('value', 'unknown') if fhir_id_elem is not None else 'unknown'

            # Count all elements
            elem_counts: Dict[str, int] = {}
            for elem in root.iter():
                tag = _local(elem.tag)
                elem_counts[tag] = elem_counts.get(tag, 0) + 1

            meta: Dict[str, Any] = {
                'fhir_version': 'R4',
                'resource_type': resource_type,
                'resource_id': fhir_id,
                'element_count': sum(elem_counts.values()),
                'has_patient': 'Patient' in elem_counts or resource_type == 'Patient',
                'patient_demographics': 'ANONYMIZED' if (
                    'Patient' in elem_counts or resource_type == 'Patient'
                ) else None,
                'parser': 'stdlib_xml',
            }
            description = (
                f"FHIR R4 XML resource — type: {resource_type}\n"
                f"ID: {fhir_id}\n"
                f"Elements: {meta['element_count']}"
            )
            return meta, description

        result = await loop.run_in_executor(self.executor, _parse)
        meta, description = result
        if meta is None:
            return ProcessingResult(
                success=False,
                error_message=f"FHIR XML parse error: {description}",
                processor_used=self.processor_name,
            )
        return ProcessingResult(
            success=True,
            extracted_text=description,
            metadata=meta,
            processor_used=self.processor_name,
            capabilities_used=['stdlib_xml'],
        )

    # ── CDA ───────────────────────────────────────────────────────────────────

    async def _process_cda(self, file_path: Path) -> ProcessingResult:
        """Parse CDA r2 Clinical Document Architecture XML."""
        loop = asyncio.get_event_loop()

        def _parse():
            try:
                tree = ET.parse(str(file_path))
                root = tree.getroot()
            except ET.ParseError as exc:
                return None, str(exc)

            # CDA namespace
            ns_prefix = ''
            if root.tag.startswith('{'):
                ns_prefix = root.tag.split('}')[0] + '}'

            def _find(xpath: str):
                return root.find(f'{ns_prefix}{xpath}' if ns_prefix else xpath)

            def _findall(xpath: str):
                return root.findall(f'{ns_prefix}{xpath}' if ns_prefix else xpath)

            # Document metadata — CDA header elements
            title_elem = _find('title')
            title = title_elem.text if title_elem is not None else 'unknown'

            effective_time_elem = _find('effectiveTime')
            effective_time = (
                effective_time_elem.get('value', 'unknown')
                if effective_time_elem is not None else 'unknown'
            )

            # Confidentiality
            conf_elem = _find('confidentialityCode')
            confidentiality = (
                conf_elem.get('code', 'unknown')
                if conf_elem is not None else 'unknown'
            )

            # Template IDs (document type)
            template_ids = [
                elem.get('root', '')
                for elem in _findall('templateId')
            ]

            # Section count
            sections = _findall('.//section') or _findall('.//component//section')
            section_count = len(sections)

            # Section titles
            section_titles = []
            for sec in sections[:10]:
                t = sec.find(f'{ns_prefix}title') if ns_prefix else sec.find('title')
                if t is not None and t.text:
                    section_titles.append(t.text.strip())

            # Extract narrative text from all sections (the CDA human-readable part)
            narrative_parts: List[str] = []
            for sec in sections:
                text_elem = sec.find(f'{ns_prefix}text') if ns_prefix else sec.find('text')
                if text_elem is not None:
                    # Collect all text nodes inside
                    inner_text = ' '.join(
                        (t.strip() for t in text_elem.itertext() if t.strip()),
                    )
                    if inner_text:
                        narrative_parts.append(inner_text[:500])

            meta: Dict[str, Any] = {
                'document_type': 'CDA_r2',
                'title': title,
                'effective_time': effective_time,
                'confidentiality': confidentiality,
                'template_ids': template_ids,
                'section_count': section_count,
                'section_titles': section_titles,
                'has_patient_data': True,  # CDA documents always have a record target
                'patient_demographics': 'ANONYMIZED',
                'parser': 'stdlib_xml',
            }
            description_lines = [
                f"CDA r2 Clinical Document — {title}",
                f"Effective time: {effective_time}",
                f"Confidentiality: {confidentiality}",
                f"Sections ({section_count}): {', '.join(section_titles[:5])}",
            ]
            if narrative_parts:
                description_lines.append("\nNarrative excerpt:")
                description_lines.append(narrative_parts[0][:300])

            return meta, '\n'.join(description_lines)

        result = await loop.run_in_executor(self.executor, _parse)
        meta, description = result
        if meta is None:
            return ProcessingResult(
                success=False,
                error_message=f"CDA XML parse error: {description}",
                processor_used=self.processor_name,
            )
        return ProcessingResult(
            success=True,
            extracted_text=description,
            metadata=meta,
            processor_used=self.processor_name,
            capabilities_used=['stdlib_xml'],
        )

    # ── Content-based fallback ─────────────────────────────────────────────────

    async def _process_by_content_inspection(self, file_path: Path) -> ProcessingResult:
        """Inspect file content to dispatch to the right sub-parser."""
        loop = asyncio.get_event_loop()
        header = await loop.run_in_executor(
            self.executor,
            lambda: file_path.read_bytes()[:512]
        )
        text_header = header.decode('utf-8', errors='replace').lstrip()

        if text_header.startswith('MSH|'):
            return await self._process_hl7v2(file_path)
        if text_header.startswith('<'):
            # Try to detect CDA vs HL7v3 vs FHIR XML by root element
            first_tag = text_header.split('>')[0].lstrip('<').split()[0].lower()
            if 'clinicaldocument' in first_tag:
                return await self._process_cda(file_path)
            elif 'bundle' in first_tag or 'patient' in first_tag:
                return await self._parse_fhir_xml(
                    header.decode('utf-8', errors='replace'), file_path
                )
            else:
                return await self._process_hl7v3_xml(file_path)

        # JSON-based FHIR
        if text_header.startswith('{'):
            try:
                parsed = json.loads(file_path.read_text(encoding='utf-8', errors='replace'))
                return await self._parse_fhir_json(parsed, file_path)
            except Exception:
                pass

        basic_meta = await self.extract_basic_metadata(file_path)
        return ProcessingResult(
            success=True,
            extracted_text=f"Healthcare file (format undetected): {file_path.name}",
            metadata=basic_meta,
            processor_used=f"{self.processor_name}_fallback",
        )


class FallbackProcessor(BaseFileProcessor):
    """Fallback processor for unknown or unsupported files"""
    
    def can_process(self, file_path: Path, mime_type: str) -> bool:
        return True  # Always available as fallback
    
    async def process_file(self, file_path: Path, metadata: Dict[str, Any]) -> ProcessingResult:
        """Fallback processing for any file"""
        start_time = time.time()
        
        try:
            basic_metadata = await self.extract_basic_metadata(file_path)
            
            # Try to detect if it's a text file
            text_content = await self._attempt_text_extraction(file_path)
            
            # Generate file signature
            signature = await self._generate_file_signature(file_path)
            
            basic_metadata.update({
                'fallback_processing': True,
                'file_signature': signature,
                'detected_as_text': bool(text_content.strip())
            })
            
            description = f"File: {file_path.name}"
            if text_content.strip():
                description += f"\n\nExtracted content:\n{text_content[:500]}"
                if len(text_content) > 500:
                    description += "..."
            
            return ProcessingResult(
                success=True,
                extracted_text=description,
                metadata=basic_metadata,
                processing_time=time.time() - start_time,
                processor_used=self.processor_name
            )
        
        except Exception as e:
            logger.error(f"Fallback processing failed: {e}")
            return ProcessingResult(
                success=False,
                error_message=str(e),
                processing_time=time.time() - start_time,
                processor_used=self.processor_name
            )
    
    async def _attempt_text_extraction(self, file_path: Path) -> str:
        """Attempt to extract text from unknown file"""
        try:
            # Try UTF-8 first
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                content = await f.read(10000)  # Read first 10KB
                # Check if it's readable text
                if self._is_readable_text(content):
                    return content
        except UnicodeDecodeError:
            pass
        
        # Try other encodings
        for encoding in ['latin1', 'cp1252', 'ascii']:
            try:
                async with aiofiles.open(file_path, 'r', encoding=encoding) as f:
                    content = await f.read(10000)
                    if self._is_readable_text(content):
                        return content
            except (UnicodeDecodeError, OSError, IOError) as e:
                logger.debug(f"Text extraction with {encoding} failed: {e}")
                continue
        
        return ""
    
    def _is_readable_text(self, content: str) -> bool:
        """Check if content appears to be readable text"""
        if not content:
            return False
        
        # Check for reasonable ratio of printable characters
        printable_chars = sum(1 for c in content if c.isprintable() or c.isspace())
        ratio = printable_chars / len(content)
        
        return ratio > 0.7  # 70% printable characters
    
    async def _generate_file_signature(self, file_path: Path) -> str:
        """Generate file signature from first bytes"""
        try:
            async with aiofiles.open(file_path, 'rb') as f:
                first_bytes = await f.read(16)
                return first_bytes.hex()
        except (OSError, IOError, PermissionError) as e:
            logger.debug(f"Failed to read file signature: {e}")
            return "unknown"
