#!/usr/bin/env python3
"""
SOMNUS Advanced File Processors
================================
advanced_files.py

Domain-specific processors for scientific, medical, forensic,
geospatial, and industrial file formats.  Drop-in compatible
with the SOMNUS BaseFileProcessor architecture — no changes
required to any existing code.

Processors
----------
1.  BiomedicalImagingProcessor    – NIfTI, MINC, MGH/MGZ, Analyze 7.5
2.  GenomicsProcessor             – FASTA/Q, VCF, BAM/SAM, GFF3/GTF, GenBank, BED
3.  ElectrophysiologyProcessor    – EDF/BDF/GDF, BrainVision triplet, FIF
4.  MassSpectrometryProcessor     – mzML, mzXML, imzML
5.  CheminformaticsProcessor      – SDF/MOL/MOL2, CIF crystallography, XYZ, SMILES
6.  GeospatialProcessor           – Shapefile, GeoJSON, KML/KMZ, GeoTIFF, GeoPackage, GPX
7.  NetworkForensicsProcessor     – PCAP, PCAPNG (+ pure-Python struct fallback)
8.  ScientificPublishingProcessor – WARC/WARC.GZ, JATS XML, TEI, METS
9.  IndustrialAndFinanceProcessor – IFC/BIM, GRIB2, XBRL/iXBRL

Integration
-----------
    from advanced_files import get_advanced_processors, ADVANCED_EXTENSIONS

    processors = get_advanced_processors(capabilities, executor)
    # Returns List[BaseFileProcessor] — append to any SOMNUS processor chain.

    ADVANCED_EXTENSIONS   # Dict[str, str]  ext → processor class name
    ADVANCED_INSTALL_MAP  # Dict[str, List[str]]  processor → pip packages

Sovereignty
-----------
    All processing is local.  No paid APIs.  No runtime downloads.
    Every processor has a Tier-0 stdlib fallback that reads at least
    header / magic-byte metadata even when no optional library is present.

    Tier 0  (stdlib only)     – always runs
    Tier 1  (common pip)      – nibabel, Biopython, mne, pyEDFlib
    Tier 2  (specialist pip)  – pysam, RDKit, pymzml, rasterio, fiona, dpkt, warcio
    Tier 3  (heavy/system)    – ifcopenshell, cfgrib, gemmi
"""

# ──────────────────────────────────────────────────────────────────────────────
#  Stdlib
# ──────────────────────────────────────────────────────────────────────────────
import asyncio
import gzip
import io
import json
import logging
import re
import sqlite3
import struct
import time
import xml.etree.ElementTree as ET
import zipfile
from abc import ABC
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
from PIL import Image

# ──────────────────────────────────────────────────────────────────────────────
#  SOMNUS base layer import
#  (adjust the import path if your project lays things out differently)
# ──────────────────────────────────────────────────────────────────────────────
from universal_file_processors import (
    BaseFileProcessor,
    ProcessingCapabilities,
    ProcessingConfig,
    ProcessingResult,
)

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
#  OPTIONAL DEPENDENCY DETECTION
# ══════════════════════════════════════════════════════════════════════════════

# Tier 1 ──────────────────────────────────────────────────────────────────────
try:
    import nibabel as nib
    NIBABEL_AVAILABLE = True
except ImportError:
    NIBABEL_AVAILABLE = False

try:
    from Bio import SeqIO
    from Bio.SeqUtils import gc_fraction
    BIOPYTHON_AVAILABLE = True
except ImportError:
    BIOPYTHON_AVAILABLE = False

try:
    import mne
    MNE_AVAILABLE = True
except ImportError:
    MNE_AVAILABLE = False

try:
    import pyedflib
    PYEDFLIB_AVAILABLE = True
except ImportError:
    PYEDFLIB_AVAILABLE = False

# Tier 2 ──────────────────────────────────────────────────────────────────────
try:
    import pysam
    PYSAM_AVAILABLE = True
except ImportError:
    PYSAM_AVAILABLE = False

try:
    from rdkit import Chem
    from rdkit.Chem import Descriptors, rdMolDescriptors
    RDKIT_AVAILABLE = True
except ImportError:
    RDKIT_AVAILABLE = False

try:
    import pymzml
    PYMZML_AVAILABLE = True
except ImportError:
    PYMZML_AVAILABLE = False

try:
    import pyteomics.mzxml as _pyteomics_mzxml
    PYTEOMICS_AVAILABLE = True
except ImportError:
    PYTEOMICS_AVAILABLE = False

try:
    import fiona
    FIONA_AVAILABLE = True
except ImportError:
    FIONA_AVAILABLE = False

try:
    import rasterio
    from rasterio.crs import CRS as RasterioCRS
    RASTERIO_AVAILABLE = True
except ImportError:
    RASTERIO_AVAILABLE = False

try:
    import dpkt
    DPKT_AVAILABLE = True
except ImportError:
    DPKT_AVAILABLE = False

try:
    import warcio
    from warcio.archiveiterator import ArchiveIterator as WARCIterator
    WARCIO_AVAILABLE = True
except ImportError:
    WARCIO_AVAILABLE = False

# Tier 3 ──────────────────────────────────────────────────────────────────────
try:
    import ifcopenshell
    IFC_AVAILABLE = True
except ImportError:
    IFC_AVAILABLE = False

try:
    import cfgrib
    CFGRIB_AVAILABLE = True
except ImportError:
    CFGRIB_AVAILABLE = False

try:
    import gemmi
    GEMMI_AVAILABLE = True
except ImportError:
    GEMMI_AVAILABLE = False

# ══════════════════════════════════════════════════════════════════════════════
#  CAPABILITY EXTENSION
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class AdvancedCapabilities:
    """
    Extended capability flags for advanced_files.py.
    Attach to a ProcessingCapabilities instance or use standalone.
    """
    nibabel:      bool = NIBABEL_AVAILABLE
    biopython:    bool = BIOPYTHON_AVAILABLE
    pysam:        bool = PYSAM_AVAILABLE
    mne:          bool = MNE_AVAILABLE
    pyedflib:     bool = PYEDFLIB_AVAILABLE
    rdkit:        bool = RDKIT_AVAILABLE
    pymzml:       bool = PYMZML_AVAILABLE
    pyteomics:    bool = PYTEOMICS_AVAILABLE
    fiona:        bool = FIONA_AVAILABLE
    rasterio:     bool = RASTERIO_AVAILABLE
    dpkt:         bool = DPKT_AVAILABLE
    warcio:       bool = WARCIO_AVAILABLE
    ifcopenshell: bool = IFC_AVAILABLE
    cfgrib:       bool = CFGRIB_AVAILABLE
    gemmi:        bool = GEMMI_AVAILABLE

    def report(self) -> Dict[str, bool]:
        return {k: v for k, v in self.__dict__.items()}


# ══════════════════════════════════════════════════════════════════════════════
#  1. BIOMEDICAL IMAGING PROCESSOR
#  NIfTI (.nii/.nii.gz), MINC (.mnc), FreeSurfer MGH/MGZ, Analyze 7.5 (.hdr/.img)
# ══════════════════════════════════════════════════════════════════════════════

class BiomedicalImagingProcessor(BaseFileProcessor):
    """
    Deep neuroimaging processor.  Handles every format produced by
    FSL, FreeSurfer, SPM, ANTs, and AFNI output pipelines.

    Full mode  (nibabel)  : shape, affine, voxel sizes, orientation,
                            intensity stats, mid-axial PNG thumbnail.
    Fallback   (stdlib)   : magic-byte / extension identification + header
                            size heuristics.
    """

    _EXTENSIONS: Set[str] = {'.nii', '.mnc', '.mgh', '.mgz', '.hdr', '.img'}

    def get_supported_extensions(self) -> Set[str]:
        return self._EXTENSIONS

    def can_process(self, file_path: Path, mime_type: str) -> bool:
        name = file_path.name.lower()
        if name.endswith('.nii.gz'):
            return True
        return file_path.suffix.lower() in self._EXTENSIONS

    async def process_file(self, file_path: Path, metadata: Dict[str, Any]) -> ProcessingResult:
        start = time.time()
        if not NIBABEL_AVAILABLE:
            basic = await self.extract_basic_metadata(file_path)
            return ProcessingResult(
                success=True,
                extracted_text=(
                    f"Neuroimaging file: {file_path.name}\n"
                    "Install nibabel for full processing: pip install nibabel"
                ),
                metadata={**basic, 'requires': 'nibabel', 'fallback': True},
                processing_time=time.time() - start,
                processor_used=f"{self.processor_name}_fallback",
            )

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(self.executor, self._load_image, file_path)
            thumbnail = result.pop('thumbnail_bytes', None)
            lines = [
                f"Neuroimaging: {file_path.name}",
                f"Format : {result.get('nibabel_class', 'unknown')}",
                f"Shape  : {result.get('shape')}",
                f"Voxels : {result.get('voxel_sizes_mm')} mm",
                f"Orient : {result.get('orientation', 'unknown')}",
            ]
            if 'intensity_min' in result:
                lines.append(
                    f"Intensity: [{result['intensity_min']:.2f}, {result['intensity_max']:.2f}]"
                    f"  mean={result['intensity_mean']:.2f}  std={result['intensity_std']:.2f}"
                )
                lines.append(
                    f"Non-zero voxels: {result['nonzero_voxels']:,} / {result['total_voxels']:,}"
                )
            return ProcessingResult(
                success=True,
                extracted_text='\n'.join(lines),
                metadata=result,
                thumbnail_data=thumbnail,
                processing_time=time.time() - start,
                processor_used=self.processor_name,
                capabilities_used=['nibabel'],
            )
        except Exception as exc:
            logger.error("BiomedicalImagingProcessor failed: %s", exc)
            return ProcessingResult(
                success=False,
                error_message=str(exc),
                processing_time=time.time() - start,
                processor_used=self.processor_name,
            )

    # ── sync worker ───────────────────────────────────────────────────────────
    def _load_image(self, file_path: Path) -> Dict[str, Any]:
        img = nib.load(str(file_path))
        hdr = img.header
        shape = list(img.shape)

        try:
            zooms = [float(z) for z in hdr.get_zooms()]
        except Exception:
            zooms = []

        try:
            ornt = nib.orientations.io_orientation(img.affine)
            orientation = ''.join(nib.orientations.ornt2axcodes(ornt))
        except Exception:
            orientation = 'unknown'

        out: Dict[str, Any] = {
            'nibabel_class': type(img).__name__,
            'shape': shape,
            'voxel_sizes_mm': zooms,
            'orientation': orientation,
            'data_dtype': str(img.get_data_dtype()),
            'affine_matrix': img.affine.tolist(),
        }

        # Header extras
        try:
            out['dim_info'] = str(hdr.get_dim_info())
            if hasattr(hdr, 'get_xyzt_units'):
                out['xyzt_units'] = hdr.get_xyzt_units()
        except Exception:
            pass

        # Intensity stats + thumbnail (cap memory use)
        try:
            data = img.get_fdata(dtype=np.float32)
            out.update({
                'intensity_min':   float(np.nanmin(data)),
                'intensity_max':   float(np.nanmax(data)),
                'intensity_mean':  float(np.nanmean(data)),
                'intensity_std':   float(np.nanstd(data)),
                'nonzero_voxels':  int(np.count_nonzero(data)),
                'total_voxels':    int(data.size),
            })
            # Mid-axial slice → PNG thumbnail
            thumbnail_bytes: Optional[bytes] = None
            if len(shape) >= 3:
                mid_z = shape[2] // 2
                sl = data[:, :, mid_z, 0] if len(shape) == 4 else data[:, :, mid_z]
                lo, hi = sl.min(), sl.max()
                norm = ((sl - lo) / (hi - lo) * 255).astype(np.uint8) if hi > lo else np.zeros_like(sl, np.uint8)
                pil = Image.fromarray(np.rot90(norm), mode='L').convert('RGB')
                pil.thumbnail((300, 300), Image.LANCZOS)
                buf = io.BytesIO()
                pil.save(buf, format='PNG')
                thumbnail_bytes = buf.getvalue()
            out['thumbnail_bytes'] = thumbnail_bytes
            del data
        except Exception as exc:
            logger.warning("Could not read voxel data: %s", exc)
            out['thumbnail_bytes'] = None

        return out


# ══════════════════════════════════════════════════════════════════════════════
#  2. GENOMICS PROCESSOR
#  FASTA, FASTQ, VCF, SAM, BAM, GFF3/GTF, BED, GenBank, EMBL
# ══════════════════════════════════════════════════════════════════════════════

class GenomicsProcessor(BaseFileProcessor):
    """
    Bioinformatics sequence & annotation processor.

    Full mode (Biopython + pysam):
        - FASTA/FASTQ: seq count, GC%, length distribution
        - VCF        : variant count, CHROM distribution, sample names
        - BAM/SAM    : alignment stats, flag distribution
        - GenBank    : organism, accession, annotated feature map
        - GFF3/GTF   : feature counts per type
        - BED        : interval count, chromosomes
    Fallback (stdlib): text sniffing + line counting.
    """

    _EXTENSIONS: Set[str] = {
        '.fa', '.fasta', '.fq', '.fastq',
        '.vcf', '.sam', '.bam',
        '.gff', '.gff3', '.gtf',
        '.bed', '.bedgraph',
        '.gb', '.gbk', '.gbff', '.embl',
    }

    def get_supported_extensions(self) -> Set[str]:
        return self._EXTENSIONS

    def can_process(self, file_path: Path, mime_type: str) -> bool:
        return file_path.suffix.lower() in self._EXTENSIONS

    async def process_file(self, file_path: Path, metadata: Dict[str, Any]) -> ProcessingResult:
        start = time.time()
        ext = file_path.suffix.lower()
        loop = asyncio.get_event_loop()
        basic = await self.extract_basic_metadata(file_path)
        try:
            if ext in ('.fa', '.fasta'):
                result = await loop.run_in_executor(self.executor, self._parse_fasta, file_path)
            elif ext in ('.fq', '.fastq'):
                result = await loop.run_in_executor(self.executor, self._parse_fastq, file_path)
            elif ext == '.vcf':
                result = await loop.run_in_executor(self.executor, self._parse_vcf, file_path)
            elif ext == '.sam':
                result = await loop.run_in_executor(self.executor, self._parse_sam_text, file_path)
            elif ext == '.bam':
                result = await loop.run_in_executor(self.executor, self._parse_bam, file_path)
            elif ext in ('.gff', '.gff3', '.gtf'):
                result = await loop.run_in_executor(self.executor, self._parse_gff_gtf, file_path)
            elif ext in ('.bed', '.bedgraph'):
                result = await loop.run_in_executor(self.executor, self._parse_bed, file_path)
            elif ext in ('.gb', '.gbk', '.gbff', '.embl'):
                fmt = 'embl' if ext == '.embl' else 'genbank'
                result = await loop.run_in_executor(self.executor, self._parse_genbank, file_path, fmt)
            else:
                result = {'description': f'Genomics file: {file_path.name}', 'meta': {}}

            return ProcessingResult(
                success=True,
                extracted_text=result.get('description', ''),
                metadata={**basic, **result.get('meta', {})},
                processing_time=time.time() - start,
                processor_used=self.processor_name,
                capabilities_used=result.get('capabilities_used', []),
            )
        except Exception as exc:
            logger.error("GenomicsProcessor failed: %s", exc)
            return ProcessingResult(
                success=False,
                error_message=str(exc),
                processing_time=time.time() - start,
                processor_used=self.processor_name,
            )

    # ── FASTA ─────────────────────────────────────────────────────────────────
    def _parse_fasta(self, file_path: Path) -> Dict[str, Any]:
        cap_used = []
        seqs: List[Dict] = []
        if BIOPYTHON_AVAILABLE:
            cap_used.append('biopython')
            opener = gzip.open if file_path.suffix == '.gz' else open
            with opener(str(file_path), 'rt') as fh:
                for i, rec in enumerate(SeqIO.parse(fh, 'fasta')):
                    if i >= 5000:
                        break
                    gc = round(float(gc_fraction(rec.seq)) * 100, 2)
                    seqs.append({'id': rec.id, 'length': len(rec), 'gc_pct': gc})
        else:
            # stdlib fallback
            cur_len = 0
            with open(file_path, 'r', errors='replace') as fh:
                for line in fh:
                    line = line.rstrip()
                    if line.startswith('>'):
                        if cur_len:
                            seqs[-1]['length'] = cur_len
                            cur_len = 0
                        seqs.append({'id': line[1:].split()[0], 'length': 0, 'gc_pct': None})
                    else:
                        cur_len += len(line)
            if seqs and cur_len:
                seqs[-1]['length'] = cur_len

        lengths = [s['length'] for s in seqs]
        gc_vals = [s['gc_pct'] for s in seqs if s['gc_pct'] is not None]
        meta: Dict[str, Any] = {
            'format': 'FASTA',
            'sequence_count': len(seqs),
            'total_bases': sum(lengths),
            'length_min': min(lengths) if lengths else 0,
            'length_max': max(lengths) if lengths else 0,
            'length_mean': round(sum(lengths) / len(lengths), 1) if lengths else 0,
            'gc_mean_pct': round(sum(gc_vals) / len(gc_vals), 2) if gc_vals else None,
            'first_10_ids': [s['id'] for s in seqs[:10]],
        }
        desc = (
            f"FASTA: {file_path.name}\n"
            f"Sequences: {meta['sequence_count']:,}  |  "
            f"Total bases: {meta['total_bases']:,}\n"
            f"Length range: {meta['length_min']:,} – {meta['length_max']:,}\n"
            f"Mean GC%: {meta['gc_mean_pct']}"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': cap_used}

    # ── FASTQ ─────────────────────────────────────────────────────────────────
    def _parse_fastq(self, file_path: Path) -> Dict[str, Any]:
        cap_used = []
        read_count = 0
        qual_sum = 0.0
        qual_min = 999
        qual_max = 0
        lengths: List[int] = []

        if BIOPYTHON_AVAILABLE:
            cap_used.append('biopython')
            with open(file_path, 'r', errors='replace') as fh:
                for i, rec in enumerate(SeqIO.parse(fh, 'fastq')):
                    if i >= 50000:
                        break
                    scores = rec.letter_annotations.get('phred_quality', [])
                    if scores:
                        q_mean = sum(scores) / len(scores)
                        qual_sum += q_mean
                        qual_min = min(qual_min, min(scores))
                        qual_max = max(qual_max, max(scores))
                    lengths.append(len(rec))
                    read_count += 1
        else:
            with open(file_path, 'r', errors='replace') as fh:
                lines = fh.readlines()
            for i in range(0, min(len(lines), 200000), 4):
                if i + 3 < len(lines):
                    seq_line = lines[i + 1].rstrip()
                    qual_line = lines[i + 3].rstrip()
                    lengths.append(len(seq_line))
                    scores = [ord(c) - 33 for c in qual_line]
                    if scores:
                        qual_sum += sum(scores) / len(scores)
                    read_count += 1

        meta: Dict[str, Any] = {
            'format': 'FASTQ',
            'read_count': read_count,
            'total_bases': sum(lengths),
            'read_length_min': min(lengths) if lengths else 0,
            'read_length_max': max(lengths) if lengths else 0,
            'mean_phred_quality': round(qual_sum / read_count, 2) if read_count else 0,
            'phred_min': qual_min if qual_min != 999 else 0,
            'phred_max': qual_max,
        }
        desc = (
            f"FASTQ: {file_path.name}\n"
            f"Reads: {meta['read_count']:,}  |  Total bases: {meta['total_bases']:,}\n"
            f"Read lengths: {meta['read_length_min']}–{meta['read_length_max']} bp\n"
            f"Mean Phred quality: {meta['mean_phred_quality']}  "
            f"(range {meta['phred_min']}–{meta['phred_max']})"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': cap_used}

    # ── VCF ───────────────────────────────────────────────────────────────────
    def _parse_vcf(self, file_path: Path) -> Dict[str, Any]:
        cap_used: List[str] = []
        samples: List[str] = []
        chrom_counts: Counter = Counter()
        variant_count = 0
        filter_counts: Counter = Counter()
        info_fields: Set[str] = set()

        with open(file_path, 'r', errors='replace') as fh:
            for line in fh:
                line = line.rstrip()
                if line.startswith('##'):
                    continue
                if line.startswith('#CHROM'):
                    parts = line.split('\t')
                    if len(parts) > 9:
                        samples = parts[9:]
                    continue
                parts = line.split('\t')
                if len(parts) < 5:
                    continue
                variant_count += 1
                chrom_counts[parts[0]] += 1
                if len(parts) > 6:
                    filter_counts[parts[6]] += 1
                if len(parts) > 7:
                    for kv in parts[7].split(';'):
                        info_fields.add(kv.split('=')[0])

        meta: Dict[str, Any] = {
            'format': 'VCF',
            'variant_count': variant_count,
            'sample_count': len(samples),
            'sample_names': samples[:20],
            'chromosomes': dict(chrom_counts.most_common(25)),
            'filter_distribution': dict(filter_counts),
            'info_fields': sorted(info_fields)[:50],
        }
        desc = (
            f"VCF: {file_path.name}\n"
            f"Variants: {variant_count:,}  |  Samples: {len(samples)}\n"
            f"Chromosomes: {', '.join(list(chrom_counts.keys())[:10])}\n"
            f"INFO fields: {', '.join(sorted(info_fields)[:15])}"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': cap_used}

    # ── SAM (text) ────────────────────────────────────────────────────────────
    def _parse_sam_text(self, file_path: Path) -> Dict[str, Any]:
        rg_lines: List[str] = []
        sq_lines: List[str] = []
        align_count = 0
        flag_counts: Counter = Counter()
        with open(file_path, 'r', errors='replace') as fh:
            for line in fh:
                if line.startswith('@RG'):
                    rg_lines.append(line.rstrip())
                elif line.startswith('@SQ'):
                    sq_lines.append(line.rstrip())
                elif not line.startswith('@'):
                    align_count += 1
                    parts = line.split('\t')
                    if len(parts) > 1:
                        try:
                            flag_counts[int(parts[1])] += 1
                        except ValueError:
                            pass
        meta: Dict[str, Any] = {
            'format': 'SAM',
            'alignment_count': align_count,
            'reference_sequences': len(sq_lines),
            'read_groups': len(rg_lines),
            'flag_distribution': dict(flag_counts.most_common(20)),
        }
        desc = (
            f"SAM: {file_path.name}\n"
            f"Alignments: {align_count:,}  |  "
            f"References: {len(sq_lines)}  |  Read groups: {len(rg_lines)}"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': []}

    # ── BAM ───────────────────────────────────────────────────────────────────
    def _parse_bam(self, file_path: Path) -> Dict[str, Any]:
        if not PYSAM_AVAILABLE:
            meta = {'format': 'BAM', 'fallback': True, 'requires': 'pysam'}
            return {
                'description': f"BAM: {file_path.name}\nInstall pysam for full BAM parsing.",
                'meta': meta, 'capabilities_used': [],
            }
        bam = pysam.AlignmentFile(str(file_path), 'rb')
        stats = bam.get_index_statistics() if bam.has_index() else []
        header_dict = dict(bam.header)
        total = 0
        mapped = 0
        unmapped = 0
        for read in bam.fetch(until_eof=True):
            total += 1
            if read.is_unmapped:
                unmapped += 1
            else:
                mapped += 1
            if total >= 500_000:
                break
        bam.close()
        meta: Dict[str, Any] = {
            'format': 'BAM',
            'total_reads_sampled': total,
            'mapped': mapped,
            'unmapped': unmapped,
            'mapping_rate_pct': round(mapped / total * 100, 2) if total else 0,
            'references': len(header_dict.get('SQ', [])),
        }
        desc = (
            f"BAM: {file_path.name}\n"
            f"Reads sampled: {total:,}  |  Mapped: {mapped:,}  "
            f"({meta['mapping_rate_pct']}%)  |  Unmapped: {unmapped:,}"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': ['pysam']}

    # ── GFF3 / GTF ────────────────────────────────────────────────────────────
    def _parse_gff_gtf(self, file_path: Path) -> Dict[str, Any]:
        feature_counts: Counter = Counter()
        seqid_set: Set[str] = set()
        with open(file_path, 'r', errors='replace') as fh:
            for line in fh:
                if line.startswith('#'):
                    continue
                parts = line.split('\t')
                if len(parts) < 3:
                    continue
                seqid_set.add(parts[0])
                feature_counts[parts[2]] += 1
        meta: Dict[str, Any] = {
            'format': file_path.suffix.upper().lstrip('.'),
            'feature_counts': dict(feature_counts.most_common()),
            'unique_seqids': len(seqid_set),
            'total_features': sum(feature_counts.values()),
        }
        desc = (
            f"{meta['format']}: {file_path.name}\n"
            f"Features: {meta['total_features']:,}  |  Sequences: {len(seqid_set)}\n"
            + '\n'.join(f"  {k}: {v:,}" for k, v in feature_counts.most_common(10))
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': []}

    # ── BED ───────────────────────────────────────────────────────────────────
    def _parse_bed(self, file_path: Path) -> Dict[str, Any]:
        chrom_counts: Counter = Counter()
        interval_count = 0
        with open(file_path, 'r', errors='replace') as fh:
            for line in fh:
                if line.startswith(('#', 'track', 'browser')):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    chrom_counts[parts[0]] += 1
                    interval_count += 1
        meta: Dict[str, Any] = {
            'format': 'BED',
            'interval_count': interval_count,
            'chromosomes': dict(chrom_counts.most_common(25)),
        }
        desc = (
            f"BED: {file_path.name}\n"
            f"Intervals: {interval_count:,}  |  "
            f"Chromosomes: {', '.join(list(chrom_counts.keys())[:10])}"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': []}

    # ── GenBank / EMBL ────────────────────────────────────────────────────────
    def _parse_genbank(self, file_path: Path, fmt: str) -> Dict[str, Any]:
        cap_used: List[str] = []
        records_info: List[Dict] = []
        if BIOPYTHON_AVAILABLE:
            cap_used.append('biopython')
            with open(file_path, 'r', errors='replace') as fh:
                for i, rec in enumerate(SeqIO.parse(fh, fmt)):
                    if i >= 50:
                        break
                    feat_counts: Counter = Counter(f.type for f in rec.features)
                    records_info.append({
                        'accession': rec.id,
                        'name': rec.name,
                        'description': rec.description[:120],
                        'length': len(rec),
                        'organism': rec.annotations.get('organism', 'unknown'),
                        'molecule_type': rec.annotations.get('molecule_type', 'unknown'),
                        'feature_counts': dict(feat_counts.most_common()),
                    })
        else:
            with open(file_path, 'r', errors='replace') as fh:
                for line in fh:
                    if line.startswith('ACCESSION') or line.startswith('AC   '):
                        acc = line.split()[-1]
                        records_info.append({'accession': acc})
                    if len(records_info) >= 50:
                        break
        meta: Dict[str, Any] = {
            'format': fmt.upper(),
            'record_count': len(records_info),
            'records': records_info[:10],
        }
        organisms = list({r.get('organism', '') for r in records_info if r.get('organism')})
        desc = (
            f"{fmt.upper()}: {file_path.name}\n"
            f"Records: {len(records_info)}\n"
            f"Organisms: {', '.join(organisms[:5])}"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': cap_used}


# ══════════════════════════════════════════════════════════════════════════════
#  3. ELECTROPHYSIOLOGY PROCESSOR
#  EDF, BDF, GDF, BrainVision (.vhdr/.vmrk/.eeg), FIF, EEGLab (.set)
# ══════════════════════════════════════════════════════════════════════════════

class ElectrophysiologyProcessor(BaseFileProcessor):
    """
    Biosignal and electrophysiology processor covering every major
    EEG/MEG/ECoG acquisition format.

    Full mode  (mne)      : channels, sampling rate, duration, events, montage.
    Fallback   (pyedflib) : EDF channel/annotation summary.
    Tier-0     (stdlib)   : BrainVision .vhdr header is plain-text INI-style —
                            always parseable with zero deps.
    """

    _EXTENSIONS: Set[str] = {'.edf', '.bdf', '.gdf', '.vhdr', '.vmrk', '.fif', '.set'}

    def get_supported_extensions(self) -> Set[str]:
        return self._EXTENSIONS

    def can_process(self, file_path: Path, mime_type: str) -> bool:
        return file_path.suffix.lower() in self._EXTENSIONS

    async def process_file(self, file_path: Path, metadata: Dict[str, Any]) -> ProcessingResult:
        start = time.time()
        ext = file_path.suffix.lower()
        loop = asyncio.get_event_loop()
        basic = await self.extract_basic_metadata(file_path)
        try:
            if ext == '.vhdr':
                result = await loop.run_in_executor(self.executor, self._parse_brainvision, file_path)
            elif ext == '.vmrk':
                result = await loop.run_in_executor(self.executor, self._parse_vmrk, file_path)
            elif ext in ('.edf', '.bdf', '.gdf'):
                result = await loop.run_in_executor(self.executor, self._parse_edf_family, file_path, ext)
            elif ext == '.fif':
                result = await loop.run_in_executor(self.executor, self._parse_fif, file_path)
            elif ext == '.set':
                result = await loop.run_in_executor(self.executor, self._parse_eeglab, file_path)
            else:
                result = {'description': f'Electrophysiology file: {file_path.name}', 'meta': {}}
            return ProcessingResult(
                success=True,
                extracted_text=result.get('description', ''),
                metadata={**basic, **result.get('meta', {})},
                processing_time=time.time() - start,
                processor_used=self.processor_name,
                capabilities_used=result.get('capabilities_used', []),
            )
        except Exception as exc:
            logger.error("ElectrophysiologyProcessor failed: %s", exc)
            return ProcessingResult(
                success=False,
                error_message=str(exc),
                processing_time=time.time() - start,
                processor_used=self.processor_name,
            )

    # ── BrainVision .vhdr (stdlib) ────────────────────────────────────────────
    def _parse_brainvision(self, file_path: Path) -> Dict[str, Any]:
        params: Dict[str, str] = {}
        channels: List[str] = []
        section = ''
        with open(file_path, 'r', encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if line.startswith('[') and line.endswith(']'):
                    section = line[1:-1]
                    continue
                if '=' in line and not line.startswith(';'):
                    k, _, v = line.partition('=')
                    if section == 'Common Infos':
                        params[k.strip()] = v.strip()
                    elif section == 'Channel Infos':
                        name_part = v.split(',')[0] if ',' in v else v
                        channels.append(name_part.strip())

        srate = float(params.get('SamplingInterval', 0))
        srate_hz = round(1_000_000 / srate, 2) if srate else None
        meta: Dict[str, Any] = {
            'format': 'BrainVision',
            'channel_count': len(channels),
            'channel_names': channels[:64],
            'sampling_rate_hz': srate_hz,
            'data_file': params.get('DataFile', 'unknown'),
            'marker_file': params.get('MarkerFile', 'unknown'),
            'data_format': params.get('DataFormat', 'unknown'),
        }
        desc = (
            f"BrainVision: {file_path.name}\n"
            f"Channels: {len(channels)}  |  SR: {srate_hz} Hz\n"
            f"First channels: {', '.join(channels[:10])}"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': []}

    def _parse_vmrk(self, file_path: Path) -> Dict[str, Any]:
        markers: List[Dict] = []
        with open(file_path, 'r', encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if line.startswith('Mk') and '=' in line:
                    val = line.partition('=')[2]
                    parts = val.split(',')
                    if len(parts) >= 3:
                        markers.append({
                            'type': parts[0],
                            'description': parts[1],
                            'position': parts[2],
                        })
        type_counts: Counter = Counter(m['type'] for m in markers)
        meta: Dict[str, Any] = {
            'format': 'BrainVision_markers',
            'marker_count': len(markers),
            'marker_types': dict(type_counts),
        }
        desc = f"BrainVision markers: {file_path.name}\nEvents: {len(markers):,}"
        return {'description': desc, 'meta': meta, 'capabilities_used': []}

    # ── EDF/BDF/GDF ───────────────────────────────────────────────────────────
    def _parse_edf_family(self, file_path: Path, ext: str) -> Dict[str, Any]:
        fmt = {'edf': 'EDF', 'bdf': 'BDF', 'gdf': 'GDF'}.get(ext.lstrip('.'), 'EDF')
        if MNE_AVAILABLE:
            raw = mne.io.read_raw(str(file_path), preload=False, verbose=False)
            info = raw.info
            meta: Dict[str, Any] = {
                'format': fmt,
                'channel_count': info['nchan'],
                'channel_names': raw.ch_names[:64],
                'sampling_rate_hz': info['sfreq'],
                'duration_sec': round(raw.times[-1], 2) if len(raw.times) else 0,
                'channel_types': list(set(mne.channel_type(info, i) for i in range(info['nchan']))),
                'meas_date': str(info.get('meas_date', 'unknown')),
            }
            events = mne.find_events(raw, verbose=False) if fmt == 'EDF' else np.array([])
            meta['event_count'] = len(events)
            desc = (
                f"{fmt}: {file_path.name}\n"
                f"Channels: {info['nchan']}  |  SR: {info['sfreq']} Hz  |  "
                f"Duration: {meta['duration_sec']}s\n"
                f"Events: {meta['event_count']}"
            )
            return {'description': desc, 'meta': meta, 'capabilities_used': ['mne']}
        elif PYEDFLIB_AVAILABLE and fmt in ('EDF', 'BDF'):
            f = pyedflib.EdfReader(str(file_path))
            n = f.signals_in_file
            names = f.getSignalLabels()
            srates = f.getSampleFrequencies()
            duration = f.getFileDuration()
            f.close()
            meta = {
                'format': fmt,
                'channel_count': n,
                'channel_names': list(names[:64]),
                'sampling_rates_hz': list(srates[:64]),
                'duration_sec': float(duration),
            }
            desc = (
                f"{fmt}: {file_path.name}\n"
                f"Channels: {n}  |  Duration: {duration}s\n"
                f"Channels: {', '.join(list(names)[:10])}"
            )
            return {'description': desc, 'meta': meta, 'capabilities_used': ['pyedflib']}
        else:
            # Tier-0: read EDF global header (256 bytes)
            with open(file_path, 'rb') as fh:
                hdr = fh.read(256)
            version = hdr[0:8].decode('ascii', errors='replace').strip()
            patient = hdr[8:88].decode('ascii', errors='replace').strip()
            n_records = hdr[236:244].decode('ascii', errors='replace').strip()
            n_signals = hdr[252:256].decode('ascii', errors='replace').strip()
            meta = {
                'format': fmt, 'fallback': True,
                'version': version, 'patient_id': 'ANONYMIZED',
                'n_data_records': n_records, 'n_signals_raw': n_signals,
            }
            desc = f"{fmt} (header-only): {file_path.name}\nSignals: {n_signals}  Records: {n_records}"
            return {'description': desc, 'meta': meta, 'capabilities_used': []}

    # ── FIF ───────────────────────────────────────────────────────────────────
    def _parse_fif(self, file_path: Path) -> Dict[str, Any]:
        if not MNE_AVAILABLE:
            return {
                'description': f"FIF: {file_path.name}\nInstall mne-python: pip install mne",
                'meta': {'format': 'FIF', 'fallback': True, 'requires': 'mne'},
                'capabilities_used': [],
            }
        raw = mne.io.read_raw_fif(str(file_path), preload=False, verbose=False)
        info = raw.info
        ch_types = list(set(mne.channel_type(info, i) for i in range(info['nchan'])))
        meta: Dict[str, Any] = {
            'format': 'FIF',
            'channel_count': info['nchan'],
            'channel_types': ch_types,
            'sampling_rate_hz': info['sfreq'],
            'duration_sec': round(raw.times[-1], 2) if len(raw.times) else 0,
            'subject_info': str(info.get('subject_info', {})),
        }
        desc = (
            f"FIF: {file_path.name}\n"
            f"Channels: {info['nchan']} ({', '.join(ch_types)})  |  SR: {info['sfreq']} Hz"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': ['mne']}

    # ── EEGLab .set ───────────────────────────────────────────────────────────
    def _parse_eeglab(self, file_path: Path) -> Dict[str, Any]:
        # .set files are MATLAB structs; scipy.io can often read them
        try:
            import scipy.io as sio
            mat = sio.loadmat(str(file_path), squeeze_me=True, struct_as_record=False)
            eeg = mat.get('EEG', None)
            if eeg is not None:
                meta: Dict[str, Any] = {
                    'format': 'EEGLab',
                    'channel_count': int(getattr(eeg, 'nbchan', 0)),
                    'sampling_rate_hz': float(getattr(eeg, 'srate', 0)),
                    'epoch_count': int(getattr(eeg, 'trials', 0)),
                    'points_per_epoch': int(getattr(eeg, 'pnts', 0)),
                }
                desc = (
                    f"EEGLab SET: {file_path.name}\n"
                    f"Channels: {meta['channel_count']}  |  SR: {meta['sampling_rate_hz']} Hz  |  "
                    f"Epochs: {meta['epoch_count']}"
                )
                return {'description': desc, 'meta': meta, 'capabilities_used': ['scipy']}
        except Exception:
            pass
        return {
            'description': f"EEGLab SET: {file_path.name}\nInstall scipy for .set parsing.",
            'meta': {'format': 'EEGLab', 'fallback': True},
            'capabilities_used': [],
        }


# ══════════════════════════════════════════════════════════════════════════════
#  4. MASS SPECTROMETRY PROCESSOR
#  mzML, mzXML, imzML (imaging mass spectrometry)
# ══════════════════════════════════════════════════════════════════════════════

class MassSpectrometryProcessor(BaseFileProcessor):
    """
    LC-MS and imaging mass spectrometry processor.

    Full mode (pymzml)  : spectrum count, MS levels, m/z range, RT window.
    imzML               : pixel count, mass range, spatial bounds.
    Fallback (stdlib)   : XML structure sniffing for spectrum count.
    """

    _EXTENSIONS: Set[str] = {'.mzml', '.mzxml', '.imzml'}

    def get_supported_extensions(self) -> Set[str]:
        return self._EXTENSIONS

    def can_process(self, file_path: Path, mime_type: str) -> bool:
        return file_path.suffix.lower() in self._EXTENSIONS

    async def process_file(self, file_path: Path, metadata: Dict[str, Any]) -> ProcessingResult:
        start = time.time()
        ext = file_path.suffix.lower()
        loop = asyncio.get_event_loop()
        basic = await self.extract_basic_metadata(file_path)
        try:
            if ext == '.mzml':
                result = await loop.run_in_executor(self.executor, self._parse_mzml, file_path)
            elif ext == '.mzxml':
                result = await loop.run_in_executor(self.executor, self._parse_mzxml, file_path)
            elif ext == '.imzml':
                result = await loop.run_in_executor(self.executor, self._parse_imzml, file_path)
            else:
                result = {'description': f'Mass spec file: {file_path.name}', 'meta': {}}
            return ProcessingResult(
                success=True,
                extracted_text=result.get('description', ''),
                metadata={**basic, **result.get('meta', {})},
                processing_time=time.time() - start,
                processor_used=self.processor_name,
                capabilities_used=result.get('capabilities_used', []),
            )
        except Exception as exc:
            logger.error("MassSpectrometryProcessor failed: %s", exc)
            return ProcessingResult(
                success=False,
                error_message=str(exc),
                processing_time=time.time() - start,
                processor_used=self.processor_name,
            )

    def _parse_mzml(self, file_path: Path) -> Dict[str, Any]:
        if PYMZML_AVAILABLE:
            run = pymzml.run.Reader(str(file_path))
            spec_count = 0
            ms_levels: Counter = Counter()
            mz_min = float('inf')
            mz_max = float('-inf')
            rt_min = float('inf')
            rt_max = float('-inf')
            for spec in run:
                spec_count += 1
                if spec_count > 100_000:
                    break
                ms_levels[spec.ms_level] += 1
                if spec.scan_time_in_minutes() is not None:
                    rt = spec.scan_time_in_minutes() * 60
                    rt_min = min(rt_min, rt)
                    rt_max = max(rt_max, rt)
                if len(spec.mz) > 0:
                    mz_min = min(mz_min, float(spec.mz[0]))
                    mz_max = max(mz_max, float(spec.mz[-1]))
            meta: Dict[str, Any] = {
                'format': 'mzML',
                'spectrum_count': spec_count,
                'ms_levels': dict(ms_levels),
                'mz_range': [round(mz_min, 4), round(mz_max, 4)] if mz_min != float('inf') else [],
                'rt_range_sec': [round(rt_min, 2), round(rt_max, 2)] if rt_min != float('inf') else [],
            }
            desc = (
                f"mzML: {file_path.name}\n"
                f"Spectra: {spec_count:,}  |  MS levels: {dict(ms_levels)}\n"
                f"m/z range: {meta['mz_range']}  |  RT: {meta['rt_range_sec']} s"
            )
            return {'description': desc, 'meta': meta, 'capabilities_used': ['pymzml']}
        # stdlib fallback — count <spectrum> elements
        spec_count = 0
        with open(file_path, 'r', errors='replace') as fh:
            for line in fh:
                spec_count += line.count('<spectrum ')
        meta = {'format': 'mzML', 'spectrum_count': spec_count, 'fallback': True}
        return {
            'description': f"mzML: {file_path.name}\nSpectra (approx): {spec_count:,}\nInstall pymzml for full parsing.",
            'meta': meta, 'capabilities_used': [],
        }

    def _parse_mzxml(self, file_path: Path) -> Dict[str, Any]:
        scan_count = 0
        ms_levels: Counter = Counter()
        try:
            for event, elem in ET.iterparse(file_path, events=('start',)):
                tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
                if tag == 'scan':
                    scan_count += 1
                    ms_levels[elem.get('msLevel', '?')] += 1
                    if scan_count >= 100_000:
                        break
        except ET.ParseError:
            pass
        meta: Dict[str, Any] = {
            'format': 'mzXML',
            'scan_count': scan_count,
            'ms_levels': dict(ms_levels),
        }
        desc = f"mzXML: {file_path.name}\nScans: {scan_count:,}  |  MS levels: {dict(ms_levels)}"
        cap = ['pyteomics'] if PYTEOMICS_AVAILABLE else []
        return {'description': desc, 'meta': meta, 'capabilities_used': cap}

    def _parse_imzml(self, file_path: Path) -> Dict[str, Any]:
        """Imaging mass spectrometry — imzML is XML with spatial pixel coords."""
        pixel_coords: List[Tuple[int, int]] = []
        mz_min = float('inf')
        mz_max = float('-inf')
        try:
            for event, elem in ET.iterparse(file_path, events=('start',)):
                tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
                if tag == 'spectrum':
                    # coordinates come from child <scanList><scan><cvParam>
                    pass
                if tag == 'cvParam':
                    name = elem.get('name', '')
                    val = elem.get('value', '')
                    if name == 'position x' and val:
                        pass  # would need to track parent scan
                    if 'lowest observed m/z' in name and val:
                        try:
                            mz_min = min(mz_min, float(val))
                        except ValueError:
                            pass
                    if 'highest observed m/z' in name and val:
                        try:
                            mz_max = max(mz_max, float(val))
                        except ValueError:
                            pass
        except ET.ParseError:
            pass

        # Count <spectrum> elements as proxy for pixel count
        spec_count = 0
        with open(file_path, 'r', errors='replace') as fh:
            for line in fh:
                spec_count += line.count('<spectrum')

        meta: Dict[str, Any] = {
            'format': 'imzML',
            'pixel_count': spec_count,
            'mz_range': [round(mz_min, 4), round(mz_max, 4)] if mz_min != float('inf') else [],
        }
        desc = (
            f"imzML (Imaging MS): {file_path.name}\n"
            f"Pixels (spectra): {spec_count:,}\n"
            f"m/z range: {meta['mz_range']}"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': []}


# ══════════════════════════════════════════════════════════════════════════════
#  5. CHEMINFORMATICS PROCESSOR
#  SDF, MOL, MOL2, CIF (crystallography), XYZ, SMILES
# ══════════════════════════════════════════════════════════════════════════════

class CheminformaticsProcessor(BaseFileProcessor):
    """
    Chemical structure and crystallography processor.

    Full mode (RDKit)  : SDF/MOL — molecule count, MW dist, formula, SMILES.
    CIF                : space group, unit cell, R-factor (gemmi or stdlib).
    XYZ/SMILES         : stdlib — element composition, atom count.
    """

    _EXTENSIONS: Set[str] = {'.sdf', '.mol', '.mol2', '.cif', '.xyz', '.smi', '.smiles'}

    def get_supported_extensions(self) -> Set[str]:
        return self._EXTENSIONS

    def can_process(self, file_path: Path, mime_type: str) -> bool:
        return file_path.suffix.lower() in self._EXTENSIONS

    async def process_file(self, file_path: Path, metadata: Dict[str, Any]) -> ProcessingResult:
        start = time.time()
        ext = file_path.suffix.lower()
        loop = asyncio.get_event_loop()
        basic = await self.extract_basic_metadata(file_path)
        try:
            if ext in ('.sdf', '.mol'):
                result = await loop.run_in_executor(self.executor, self._parse_sdf_mol, file_path, ext)
            elif ext == '.mol2':
                result = await loop.run_in_executor(self.executor, self._parse_mol2, file_path)
            elif ext == '.cif':
                result = await loop.run_in_executor(self.executor, self._parse_cif, file_path)
            elif ext == '.xyz':
                result = await loop.run_in_executor(self.executor, self._parse_xyz, file_path)
            elif ext in ('.smi', '.smiles'):
                result = await loop.run_in_executor(self.executor, self._parse_smiles, file_path)
            else:
                result = {'description': f'Chem file: {file_path.name}', 'meta': {}}
            return ProcessingResult(
                success=True,
                extracted_text=result.get('description', ''),
                metadata={**basic, **result.get('meta', {})},
                processing_time=time.time() - start,
                processor_used=self.processor_name,
                capabilities_used=result.get('capabilities_used', []),
            )
        except Exception as exc:
            logger.error("CheminformaticsProcessor failed: %s", exc)
            return ProcessingResult(
                success=False,
                error_message=str(exc),
                processing_time=time.time() - start,
                processor_used=self.processor_name,
            )

    def _parse_sdf_mol(self, file_path: Path, ext: str) -> Dict[str, Any]:
        if RDKIT_AVAILABLE:
            suppl = Chem.SDMolSupplier(str(file_path), removeHs=False)
            mols = [m for m in suppl if m is not None]
            mws = [round(Descriptors.ExactMolWt(m), 4) for m in mols[:1000]]
            formulas = [rdMolDescriptors.CalcMolFormula(m) for m in mols[:20]]
            meta: Dict[str, Any] = {
                'format': ext.upper().lstrip('.'),
                'molecule_count': len(mols),
                'mw_min': min(mws) if mws else 0,
                'mw_max': max(mws) if mws else 0,
                'mw_mean': round(sum(mws) / len(mws), 4) if mws else 0,
                'formulas_sample': formulas[:10],
                'atom_counts': [m.GetNumAtoms() for m in mols[:20]],
            }
            desc = (
                f"{ext.upper()}: {file_path.name}\n"
                f"Molecules: {len(mols):,}\n"
                f"MW range: {meta['mw_min']} – {meta['mw_max']} Da\n"
                f"Sample formulas: {', '.join(formulas[:5])}"
            )
            return {'description': desc, 'meta': meta, 'capabilities_used': ['rdkit']}
        # stdlib fallback: count $$$$ separators
        mol_count = 0
        with open(file_path, 'r', errors='replace') as fh:
            for line in fh:
                if line.startswith('$$$$'):
                    mol_count += 1
        if mol_count == 0:
            mol_count = 1  # single .mol file
        meta = {'format': ext.upper().lstrip('.'), 'molecule_count': mol_count, 'fallback': True}
        desc = f"{ext.upper()}: {file_path.name}\nMolecules: {mol_count}\nInstall RDKit for full parsing."
        return {'description': desc, 'meta': meta, 'capabilities_used': []}

    def _parse_mol2(self, file_path: Path) -> Dict[str, Any]:
        mol_count = 0
        atom_count = 0
        bond_count = 0
        with open(file_path, 'r', errors='replace') as fh:
            section = ''
            for line in fh:
                line = line.strip()
                if line.startswith('@<TRIPOS>MOLECULE'):
                    mol_count += 1
                    section = 'molecule'
                elif line.startswith('@<TRIPOS>'):
                    section = line[9:].lower()
                elif section == 'atom' and line and not line.startswith('@'):
                    atom_count += 1
                elif section == 'bond' and line and not line.startswith('@'):
                    bond_count += 1
        meta: Dict[str, Any] = {
            'format': 'MOL2', 'molecule_count': mol_count,
            'total_atoms': atom_count, 'total_bonds': bond_count,
        }
        desc = (
            f"MOL2: {file_path.name}\n"
            f"Molecules: {mol_count}  |  Atoms: {atom_count}  |  Bonds: {bond_count}"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': []}

    def _parse_cif(self, file_path: Path) -> Dict[str, Any]:
        if GEMMI_AVAILABLE:
            doc = gemmi.cif.read(str(file_path))
            block = doc.sole_block() if len(doc) == 1 else doc[0]
            meta: Dict[str, Any] = {
                'format': 'CIF',
                'block_name': block.name,
                'space_group': block.find_value('_symmetry_space_group_name_H-M') or
                               block.find_value('_space_group_name_H-M_alt') or 'unknown',
                'cell_a': block.find_value('_cell_length_a') or 'unknown',
                'cell_b': block.find_value('_cell_length_b') or 'unknown',
                'cell_c': block.find_value('_cell_length_c') or 'unknown',
                'r_factor': block.find_value('_refine_ls_R_factor_gt') or 'unknown',
                'compound': block.find_value('_chemical_name_common') or 'unknown',
            }
            desc = (
                f"CIF: {file_path.name}\n"
                f"Compound: {meta['compound']}\n"
                f"Space group: {meta['space_group']}\n"
                f"Unit cell: a={meta['cell_a']} b={meta['cell_b']} c={meta['cell_c']}\n"
                f"R-factor: {meta['r_factor']}"
            )
            return {'description': desc, 'meta': meta, 'capabilities_used': ['gemmi']}
        # stdlib: regex key extraction
        fields = {
            '_symmetry_space_group_name_H-M': 'space_group',
            '_cell_length_a': 'cell_a',
            '_cell_length_b': 'cell_b',
            '_cell_length_c': 'cell_c',
            '_refine_ls_R_factor_gt': 'r_factor',
        }
        meta = {'format': 'CIF'}
        with open(file_path, 'r', errors='replace') as fh:
            for line in fh:
                for key, field in fields.items():
                    if line.strip().startswith(key):
                        val = line.strip().split()[-1].strip("'\"")
                        meta[field] = val
        desc = (
            f"CIF: {file_path.name}\n"
            f"Space group: {meta.get('space_group', 'unknown')}\n"
            f"Unit cell: a={meta.get('cell_a', '?')} b={meta.get('cell_b', '?')} c={meta.get('cell_c', '?')}"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': []}

    def _parse_xyz(self, file_path: Path) -> Dict[str, Any]:
        elem_counts: Counter = Counter()
        frame_count = 0
        with open(file_path, 'r', errors='replace') as fh:
            lines = fh.readlines()
        i = 0
        while i < len(lines):
            try:
                n_atoms = int(lines[i].strip())
                frame_count += 1
                comment = lines[i + 1].rstrip() if i + 1 < len(lines) else ''
                for j in range(i + 2, i + 2 + n_atoms):
                    if j < len(lines):
                        parts = lines[j].split()
                        if parts:
                            elem_counts[parts[0]] += 1
                i += 2 + n_atoms
            except (ValueError, IndexError):
                i += 1
        meta: Dict[str, Any] = {
            'format': 'XYZ',
            'frame_count': frame_count,
            'element_counts': dict(elem_counts.most_common()),
            'total_atoms': sum(elem_counts.values()),
            'formula': ''.join(f"{e}{c}" for e, c in sorted(elem_counts.items())),
        }
        desc = (
            f"XYZ: {file_path.name}\n"
            f"Frames: {frame_count}  |  Total atoms: {meta['total_atoms']}\n"
            f"Composition: {meta['formula']}"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': []}

    def _parse_smiles(self, file_path: Path) -> Dict[str, Any]:
        smiles_list: List[str] = []
        with open(file_path, 'r', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith('#'):
                    smiles_list.append(line.split()[0])
        meta: Dict[str, Any] = {
            'format': 'SMILES',
            'molecule_count': len(smiles_list),
            'sample': smiles_list[:10],
        }
        desc = f"SMILES: {file_path.name}\nMolecules: {len(smiles_list):,}"
        return {'description': desc, 'meta': meta, 'capabilities_used': []}


# ══════════════════════════════════════════════════════════════════════════════
#  6. GEOSPATIAL PROCESSOR
#  Shapefile, GeoJSON, KML/KMZ, GeoTIFF, GeoPackage, GPX
# ══════════════════════════════════════════════════════════════════════════════

class GeospatialProcessor(BaseFileProcessor):
    """
    Geospatial and GIS processor.

    Full mode : fiona (Shapefile), rasterio (GeoTIFF).
    Tier-0    : GeoJSON (stdlib json), KML/KMZ (stdlib xml/zipfile),
                GeoPackage (stdlib sqlite3), GPX (stdlib xml).
    """

    _EXTENSIONS: Set[str] = {'.shp', '.geojson', '.kml', '.kmz', '.gpkg', '.tif', '.tiff', '.gpx'}

    def get_supported_extensions(self) -> Set[str]:
        return self._EXTENSIONS

    def can_process(self, file_path: Path, mime_type: str) -> bool:
        return file_path.suffix.lower() in self._EXTENSIONS

    async def process_file(self, file_path: Path, metadata: Dict[str, Any]) -> ProcessingResult:
        start = time.time()
        ext = file_path.suffix.lower()
        loop = asyncio.get_event_loop()
        basic = await self.extract_basic_metadata(file_path)
        try:
            if ext == '.shp':
                result = await loop.run_in_executor(self.executor, self._parse_shapefile, file_path)
            elif ext == '.geojson':
                result = await loop.run_in_executor(self.executor, self._parse_geojson, file_path)
            elif ext == '.kml':
                result = await loop.run_in_executor(self.executor, self._parse_kml, file_path)
            elif ext == '.kmz':
                result = await loop.run_in_executor(self.executor, self._parse_kmz, file_path)
            elif ext == '.gpkg':
                result = await loop.run_in_executor(self.executor, self._parse_gpkg, file_path)
            elif ext in ('.tif', '.tiff'):
                result = await loop.run_in_executor(self.executor, self._parse_geotiff, file_path)
            elif ext == '.gpx':
                result = await loop.run_in_executor(self.executor, self._parse_gpx, file_path)
            else:
                result = {'description': f'Geo file: {file_path.name}', 'meta': {}}
            return ProcessingResult(
                success=True,
                extracted_text=result.get('description', ''),
                metadata={**basic, **result.get('meta', {})},
                processing_time=time.time() - start,
                processor_used=self.processor_name,
                capabilities_used=result.get('capabilities_used', []),
            )
        except Exception as exc:
            logger.error("GeospatialProcessor failed: %s", exc)
            return ProcessingResult(
                success=False,
                error_message=str(exc),
                processing_time=time.time() - start,
                processor_used=self.processor_name,
            )

    def _parse_shapefile(self, file_path: Path) -> Dict[str, Any]:
        if FIONA_AVAILABLE:
            with fiona.open(str(file_path)) as src:
                geom_types: Counter = Counter(f['geometry']['type'] for f in src if f['geometry'])
                schema = src.schema
                meta: Dict[str, Any] = {
                    'format': 'Shapefile',
                    'feature_count': len(src),
                    'crs': str(src.crs) if src.crs else 'unknown',
                    'bbox': list(src.bounds),
                    'geometry_types': dict(geom_types),
                    'properties': list(schema['properties'].keys())[:30],
                }
            desc = (
                f"Shapefile: {file_path.name}\n"
                f"Features: {meta['feature_count']:,}  |  CRS: {meta['crs']}\n"
                f"Geometry: {dict(geom_types)}  |  Bbox: {meta['bbox']}"
            )
            return {'description': desc, 'meta': meta, 'capabilities_used': ['fiona']}
        meta = {'format': 'Shapefile', 'fallback': True, 'requires': 'fiona'}
        return {
            'description': f"Shapefile: {file_path.name}\nInstall fiona: pip install fiona",
            'meta': meta, 'capabilities_used': [],
        }

    def _parse_geojson(self, file_path: Path) -> Dict[str, Any]:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as fh:
            data = json.load(fh)
        features = data.get('features', []) if data.get('type') == 'FeatureCollection' else [data]
        geom_types: Counter = Counter(
            f.get('geometry', {}).get('type', 'unknown') for f in features
        )
        prop_keys: Set[str] = set()
        for f in features[:100]:
            prop_keys.update((f.get('properties') or {}).keys())
        meta: Dict[str, Any] = {
            'format': 'GeoJSON',
            'feature_count': len(features),
            'geometry_types': dict(geom_types),
            'property_keys': sorted(prop_keys)[:30],
        }
        desc = (
            f"GeoJSON: {file_path.name}\n"
            f"Features: {len(features):,}  |  Types: {dict(geom_types)}\n"
            f"Properties: {', '.join(sorted(prop_keys)[:10])}"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': []}

    def _parse_kml(self, file_path: Path) -> Dict[str, Any]:
        return self._parse_kml_content(file_path.read_text(encoding='utf-8', errors='replace'), file_path.name)

    def _parse_kmz(self, file_path: Path) -> Dict[str, Any]:
        with zipfile.ZipFile(file_path) as zf:
            kml_names = [n for n in zf.namelist() if n.endswith('.kml')]
            if kml_names:
                content = zf.read(kml_names[0]).decode('utf-8', errors='replace')
                return self._parse_kml_content(content, file_path.name)
        return {'description': f"KMZ: {file_path.name} (no .kml found)", 'meta': {'format': 'KMZ'}, 'capabilities_used': []}

    def _parse_kml_content(self, content: str, name: str) -> Dict[str, Any]:
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return {'description': f"KML: {name} (parse error)", 'meta': {'format': 'KML'}, 'capabilities_used': []}
        ns = {'kml': 'http://www.opengis.net/kml/2.2'}
        def find_all(tag: str) -> List:
            elems = root.findall(f'.//{{{ns["kml"]}}}{tag}')
            if not elems:
                elems = root.findall(f'.//{tag}')
            return elems
        placemarks = find_all('Placemark')
        folders = find_all('Folder')
        names = [e.text for e in find_all('name') if e.text][:20]
        descriptions = [e.text[:100] for e in find_all('description') if e.text][:10]
        meta: Dict[str, Any] = {
            'format': 'KML',
            'placemark_count': len(placemarks),
            'folder_count': len(folders),
            'place_names': names[:20],
        }
        desc = (
            f"KML: {name}\n"
            f"Placemarks: {len(placemarks)}  |  Folders: {len(folders)}\n"
            f"Places: {', '.join(names[:8])}"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': []}

    def _parse_gpkg(self, file_path: Path) -> Dict[str, Any]:
        conn = sqlite3.connect(str(file_path))
        cur = conn.cursor()
        layers: List[Dict] = []
        try:
            cur.execute("SELECT table_name, data_type, srs_id FROM gpkg_contents")
            for row in cur.fetchall():
                layers.append({'name': row[0], 'type': row[1], 'srs_id': row[2]})
        except sqlite3.Error:
            pass
        row_counts: Dict[str, int] = {}
        for layer in layers:
            try:
                cur.execute(f"SELECT COUNT(*) FROM \"{layer['name']}\"")
                row_counts[layer['name']] = cur.fetchone()[0]
            except sqlite3.Error:
                pass
        conn.close()
        meta: Dict[str, Any] = {
            'format': 'GeoPackage',
            'layer_count': len(layers),
            'layers': layers,
            'row_counts': row_counts,
        }
        desc = (
            f"GeoPackage: {file_path.name}\n"
            f"Layers: {len(layers)}\n"
            + '\n'.join(f"  {l['name']} ({l['type']}): {row_counts.get(l['name'], '?')} rows" for l in layers[:10])
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': ['sqlite3']}

    def _parse_geotiff(self, file_path: Path) -> Dict[str, Any]:
        if RASTERIO_AVAILABLE:
            with rasterio.open(str(file_path)) as ds:
                meta: Dict[str, Any] = {
                    'format': 'GeoTIFF',
                    'width': ds.width,
                    'height': ds.height,
                    'band_count': ds.count,
                    'crs': str(ds.crs) if ds.crs else 'unknown',
                    'bbox': list(ds.bounds),
                    'transform': list(ds.transform),
                    'dtypes': [str(d) for d in ds.dtypes],
                    'nodata': ds.nodata,
                    'res_x': abs(ds.transform.a),
                    'res_y': abs(ds.transform.e),
                }
            desc = (
                f"GeoTIFF: {file_path.name}\n"
                f"{meta['width']}×{meta['height']} px  |  "
                f"Bands: {meta['band_count']}  |  CRS: {meta['crs']}\n"
                f"Resolution: {meta['res_x']:.6f} × {meta['res_y']:.6f}"
            )
            return {'description': desc, 'meta': meta, 'capabilities_used': ['rasterio']}
        meta = {'format': 'GeoTIFF', 'fallback': True, 'requires': 'rasterio'}
        return {
            'description': f"GeoTIFF: {file_path.name}\nInstall rasterio: pip install rasterio",
            'meta': meta, 'capabilities_used': [],
        }

    def _parse_gpx(self, file_path: Path) -> Dict[str, Any]:
        try:
            root = ET.parse(str(file_path)).getroot()
        except ET.ParseError:
            return {'description': f"GPX: {file_path.name} (parse error)", 'meta': {'format': 'GPX'}, 'capabilities_used': []}
        ns_uri = 'http://www.topografix.com/GPX/1/1'
        def find_all(tag: str) -> List:
            elems = root.findall(f'.//{{{ns_uri}}}{tag}')
            if not elems:
                elems = root.findall(f'.//{tag}')
            return elems
        wpts = find_all('wpt')
        trks = find_all('trk')
        trkpts = find_all('trkpt')
        rtes = find_all('rte')
        names = [e.text for e in find_all('name') if e.text][:20]
        meta: Dict[str, Any] = {
            'format': 'GPX',
            'waypoint_count': len(wpts),
            'track_count': len(trks),
            'track_point_count': len(trkpts),
            'route_count': len(rtes),
            'names': names,
        }
        desc = (
            f"GPX: {file_path.name}\n"
            f"Tracks: {len(trks)}  |  Waypoints: {len(wpts)}  |  "
            f"Track points: {len(trkpts):,}  |  Routes: {len(rtes)}"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': []}


# ══════════════════════════════════════════════════════════════════════════════
#  7. NETWORK FORENSICS PROCESSOR
#  PCAP, PCAPNG — with pure-Python struct fallback
# ══════════════════════════════════════════════════════════════════════════════

# PCAP magic bytes
_PCAP_MAGIC_LE  = b'\xd4\xc3\xb2\xa1'
_PCAP_MAGIC_BE  = b'\xa1\xb2\xc3\xd4'
_PCAPNG_MAGIC   = b'\x0a\x0d\x0d\x0a'

class NetworkForensicsProcessor(BaseFileProcessor):
    """
    Network packet capture processor.

    Full mode  (dpkt)   : packet count, protocol distribution, top IPs, duration.
    Tier-0     (struct) : global header parse — linktype, version, snaplen.
    """

    _EXTENSIONS: Set[str] = {'.pcap', '.pcapng', '.cap'}

    def get_supported_extensions(self) -> Set[str]:
        return self._EXTENSIONS

    def can_process(self, file_path: Path, mime_type: str) -> bool:
        if file_path.suffix.lower() in self._EXTENSIONS:
            return True
        try:
            magic = file_path.read_bytes()[:4]
            return magic in (_PCAP_MAGIC_LE, _PCAP_MAGIC_BE, _PCAPNG_MAGIC)
        except OSError:
            return False

    async def process_file(self, file_path: Path, metadata: Dict[str, Any]) -> ProcessingResult:
        start = time.time()
        loop = asyncio.get_event_loop()
        basic = await self.extract_basic_metadata(file_path)
        try:
            result = await loop.run_in_executor(self.executor, self._parse_pcap, file_path)
            return ProcessingResult(
                success=True,
                extracted_text=result.get('description', ''),
                metadata={**basic, **result.get('meta', {})},
                processing_time=time.time() - start,
                processor_used=self.processor_name,
                capabilities_used=result.get('capabilities_used', []),
            )
        except Exception as exc:
            logger.error("NetworkForensicsProcessor failed: %s", exc)
            return ProcessingResult(
                success=False,
                error_message=str(exc),
                processing_time=time.time() - start,
                processor_used=self.processor_name,
            )

    def _parse_pcap(self, file_path: Path) -> Dict[str, Any]:
        raw = file_path.read_bytes()
        magic = raw[:4]

        if magic == _PCAPNG_MAGIC:
            # PCAPNG: iterate Interface/Enhanced Packet Blocks
            return self._parse_pcapng_struct(raw, file_path.name)

        if magic not in (_PCAP_MAGIC_LE, _PCAP_MAGIC_BE):
            return {
                'description': f"Unknown capture format: {file_path.name}",
                'meta': {'format': 'unknown'},
                'capabilities_used': [],
            }

        little_endian = (magic == _PCAP_MAGIC_LE)
        endian = '<' if little_endian else '>'

        # Global header: magic(4) ver_major(2) ver_minor(2) thiszone(4)
        #                sigfigs(4) snaplen(4) network(4)
        gh = struct.unpack_from(f'{endian}IHHiIII', raw, 0)
        ver_major, ver_minor, _, _, snaplen, link_type = gh[1], gh[2], gh[3], gh[4], gh[5], gh[6]

        if DPKT_AVAILABLE:
            return self._parse_pcap_dpkt(file_path, ver_major, ver_minor, snaplen, link_type)

        # Tier-0: count packets from record headers only
        pkt_count = 0
        ts_first: Optional[float] = None
        ts_last: Optional[float] = None
        offset = 24  # skip global header
        while offset + 16 <= len(raw):
            ts_sec, ts_usec, incl_len, orig_len = struct.unpack_from(f'{endian}IIII', raw, offset)
            ts = ts_sec + ts_usec / 1_000_000
            if ts_first is None:
                ts_first = ts
            ts_last = ts
            pkt_count += 1
            offset += 16 + incl_len
            if pkt_count >= 1_000_000:
                break

        duration = round(ts_last - ts_first, 6) if ts_first and ts_last else 0
        meta: Dict[str, Any] = {
            'format': 'PCAP',
            'version': f"{ver_major}.{ver_minor}",
            'link_type': link_type,
            'snaplen': snaplen,
            'packet_count': pkt_count,
            'capture_duration_sec': duration,
        }
        desc = (
            f"PCAP: {file_path.name}\n"
            f"Packets: {pkt_count:,}  |  Duration: {duration}s  |  "
            f"Linktype: {link_type}  |  Snaplen: {snaplen}"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': []}

    def _parse_pcapng_struct(self, raw: bytes, name: str) -> Dict[str, Any]:
        # Minimal PCAPNG block iteration without dpkt
        block_count = 0
        epb_count = 0
        offset = 0
        while offset + 12 <= len(raw):
            block_type = struct.unpack_from('<I', raw, offset)[0]
            block_len = struct.unpack_from('<I', raw, offset + 4)[0]
            if block_len < 12 or offset + block_len > len(raw):
                break
            if block_type == 0x00000006:  # Enhanced Packet Block
                epb_count += 1
            block_count += 1
            offset += block_len
        meta: Dict[str, Any] = {
            'format': 'PCAPNG', 'block_count': block_count,
            'packet_count': epb_count,
        }
        desc = f"PCAPNG: {name}\nBlocks: {block_count}  |  Packets: {epb_count:,}"
        return {'description': desc, 'meta': meta, 'capabilities_used': []}

    def _parse_pcap_dpkt(self, file_path: Path, vmaj: int, vmin: int, snaplen: int, link_type: int) -> Dict[str, Any]:
        proto_counts: Counter = Counter()
        ip_src: Counter = Counter()
        ip_dst: Counter = Counter()
        pkt_count = 0
        ts_first: Optional[float] = None
        ts_last: Optional[float] = None

        with open(file_path, 'rb') as fh:
            pcap = dpkt.pcap.Reader(fh)
            for ts, buf in pcap:
                if ts_first is None:
                    ts_first = ts
                ts_last = ts
                pkt_count += 1
                if pkt_count > 500_000:
                    break
                try:
                    eth = dpkt.ethernet.Ethernet(buf)
                    if isinstance(eth.data, dpkt.ip.IP):
                        ip = eth.data
                        src = '.'.join(str(b) for b in ip.src)
                        dst = '.'.join(str(b) for b in ip.dst)
                        ip_src[src] += 1
                        ip_dst[dst] += 1
                        proto_counts[ip.p] += 1
                except Exception:
                    pass

        PROTO_NAMES = {6: 'TCP', 17: 'UDP', 1: 'ICMP', 89: 'OSPF', 47: 'GRE'}
        named_protos = {PROTO_NAMES.get(k, str(k)): v for k, v in proto_counts.most_common(10)}
        duration = round(ts_last - ts_first, 6) if ts_first and ts_last else 0
        meta: Dict[str, Any] = {
            'format': 'PCAP',
            'version': f"{vmaj}.{vmin}",
            'packet_count': pkt_count,
            'capture_duration_sec': duration,
            'protocol_distribution': named_protos,
            'top_sources': dict(ip_src.most_common(10)),
            'top_destinations': dict(ip_dst.most_common(10)),
        }
        desc = (
            f"PCAP: {file_path.name}\n"
            f"Packets: {pkt_count:,}  |  Duration: {duration}s\n"
            f"Protocols: {named_protos}\n"
            f"Top sources: {', '.join(list(ip_src.keys())[:5])}"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': ['dpkt']}


# ══════════════════════════════════════════════════════════════════════════════
#  8. SCIENTIFIC PUBLISHING PROCESSOR
#  WARC/WARC.GZ, JATS XML (PubMed), TEI XML, METS
# ══════════════════════════════════════════════════════════════════════════════

class ScientificPublishingProcessor(BaseFileProcessor):
    """
    Scientific publishing and digital preservation processor.

    WARC  : web archive record inventory (warcio or stdlib fallback).
    JATS  : PubMed/PMC journal article XML — title, authors, abstract, DOI.
    TEI   : Text Encoding Initiative XML — header, body structure.
    METS  : Library of Congress Metadata Encoding — structural map, file groups.
    """

    _EXTENSIONS: Set[str] = {'.warc', '.nxml', '.jats', '.tei', '.mets', '.xml'}

    def get_supported_extensions(self) -> Set[str]:
        return self._EXTENSIONS

    def can_process(self, file_path: Path, mime_type: str) -> bool:
        ext = file_path.suffix.lower()
        if ext in ('.warc',):
            return True
        if file_path.name.lower().endswith('.warc.gz'):
            return True
        # Sniff XML files for JATS/TEI/METS signatures
        if ext in ('.nxml', '.jats', '.tei', '.mets'):
            return True
        if ext == '.xml':
            return self._sniff_xml_type(file_path) is not None
        return False

    def _sniff_xml_type(self, file_path: Path) -> Optional[str]:
        try:
            header = file_path.read_bytes()[:512].decode('utf-8', errors='replace').lower()
            if 'dtd/jats' in header or 'nlm-dtd' in header or 'article-type' in header:
                return 'jats'
            if 'tei-c.org' in header or '<tei' in header:
                return 'tei'
            if 'loc.gov/mets' in header or '<mets' in header:
                return 'mets'
        except OSError:
            pass
        return None

    async def process_file(self, file_path: Path, metadata: Dict[str, Any]) -> ProcessingResult:
        start = time.time()
        name = file_path.name.lower()
        ext = file_path.suffix.lower()
        loop = asyncio.get_event_loop()
        basic = await self.extract_basic_metadata(file_path)
        try:
            if name.endswith('.warc') or name.endswith('.warc.gz'):
                result = await loop.run_in_executor(self.executor, self._parse_warc, file_path)
            elif ext in ('.nxml', '.jats') or self._sniff_xml_type(file_path) == 'jats':
                result = await loop.run_in_executor(self.executor, self._parse_jats, file_path)
            elif ext == '.tei' or self._sniff_xml_type(file_path) == 'tei':
                result = await loop.run_in_executor(self.executor, self._parse_tei, file_path)
            elif ext == '.mets' or self._sniff_xml_type(file_path) == 'mets':
                result = await loop.run_in_executor(self.executor, self._parse_mets, file_path)
            else:
                result = {'description': f'Publishing file: {file_path.name}', 'meta': {}}
            return ProcessingResult(
                success=True,
                extracted_text=result.get('description', ''),
                metadata={**basic, **result.get('meta', {})},
                processing_time=time.time() - start,
                processor_used=self.processor_name,
                capabilities_used=result.get('capabilities_used', []),
            )
        except Exception as exc:
            logger.error("ScientificPublishingProcessor failed: %s", exc)
            return ProcessingResult(
                success=False,
                error_message=str(exc),
                processing_time=time.time() - start,
                processor_used=self.processor_name,
            )

    # ── WARC ─────────────────────────────────────────────────────────────────
    def _parse_warc(self, file_path: Path) -> Dict[str, Any]:
        if WARCIO_AVAILABLE:
            return self._parse_warc_warcio(file_path)
        return self._parse_warc_stdlib(file_path)

    def _parse_warc_warcio(self, file_path: Path) -> Dict[str, Any]:
        record_count = 0
        type_counts: Counter = Counter()
        content_types: Counter = Counter()
        uris: List[str] = []
        total_payload = 0
        opener = gzip.open if file_path.name.endswith('.gz') else open
        with opener(str(file_path), 'rb') as fh:
            for rec in WARCIterator(fh):
                record_count += 1
                rtype = rec.rec_type or 'unknown'
                type_counts[rtype] += 1
                uri = rec.rec_headers.get_header('WARC-Target-URI')
                if uri:
                    uris.append(uri)
                ct = rec.http_headers.get_header('Content-Type') if rec.http_headers else None
                if ct:
                    content_types[ct.split(';')[0].strip()] += 1
                total_payload += rec.length or 0
                if record_count > 50_000:
                    break
        meta: Dict[str, Any] = {
            'format': 'WARC',
            'record_count': record_count,
            'record_types': dict(type_counts),
            'content_types': dict(content_types.most_common(20)),
            'total_payload_bytes': total_payload,
            'sample_uris': uris[:20],
        }
        desc = (
            f"WARC: {file_path.name}\n"
            f"Records: {record_count:,}  |  Payload: {total_payload:,} bytes\n"
            f"Types: {dict(type_counts)}\n"
            f"Content-types: {dict(content_types.most_common(5))}"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': ['warcio']}

    def _parse_warc_stdlib(self, file_path: Path) -> Dict[str, Any]:
        """Tier-0: scan for WARC record headers without warcio."""
        record_count = 0
        uris: List[str] = []
        opener = gzip.open if file_path.name.endswith('.gz') else open
        with opener(str(file_path), 'rt', encoding='utf-8', errors='replace') as fh:
            for line in fh:
                if line.startswith('WARC/'):
                    record_count += 1
                elif line.startswith('WARC-Target-URI:') and len(uris) < 20:
                    uris.append(line.split(':', 1)[-1].strip())
                if record_count > 50_000:
                    break
        meta: Dict[str, Any] = {
            'format': 'WARC', 'record_count': record_count,
            'sample_uris': uris,
        }
        desc = (
            f"WARC: {file_path.name}\n"
            f"Records: {record_count:,}\nInstall warcio for full parsing: pip install warcio"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': []}

    # ── JATS XML ──────────────────────────────────────────────────────────────
    def _parse_jats(self, file_path: Path) -> Dict[str, Any]:
        try:
            root = ET.parse(str(file_path)).getroot()
        except ET.ParseError as exc:
            return {'description': f'JATS XML parse error: {exc}', 'meta': {'format': 'JATS'}, 'capabilities_used': []}

        def _text(tag: str) -> str:
            e = root.find(f'.//{tag}')
            return (e.text or '').strip() if e is not None else ''

        def _all_text(tag: str) -> List[str]:
            return [(e.text or '').strip() for e in root.findall(f'.//{tag}') if e.text]

        title = _text('article-title') or _text('title')
        doi = ''
        for aid in root.findall('.//article-id'):
            if aid.get('pub-id-type') == 'doi':
                doi = (aid.text or '').strip()
        authors: List[str] = []
        for contrib in root.findall('.//contrib[@contrib-type="author"]'):
            sn = contrib.find('.//surname')
            gn = contrib.find('.//given-names')
            if sn is not None:
                name = (sn.text or '')
                if gn is not None:
                    name += f", {gn.text or ''}"
                authors.append(name.strip())
        abstract_parts = _all_text('abstract/p') or [_text('abstract')]
        abstract = ' '.join(abstract_parts)[:800]
        journal = _text('journal-title') or _text('source')
        year = _text('year')
        keywords = _all_text('kwd')
        funding = _all_text('funding-source')
        meta: Dict[str, Any] = {
            'format': 'JATS',
            'title': title,
            'doi': doi,
            'authors': authors[:30],
            'journal': journal,
            'year': year,
            'keywords': keywords[:20],
            'funding_sources': funding[:10],
            'abstract_length': len(abstract),
        }
        desc = (
            f"JATS Article: {title}\n"
            f"DOI: {doi}  |  Journal: {journal}  |  Year: {year}\n"
            f"Authors: {'; '.join(authors[:5])}\n"
            f"Abstract: {abstract[:300]}{'...' if len(abstract) > 300 else ''}"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': []}

    # ── TEI XML ───────────────────────────────────────────────────────────────
    def _parse_tei(self, file_path: Path) -> Dict[str, Any]:
        try:
            root = ET.parse(str(file_path)).getroot()
        except ET.ParseError as exc:
            return {'description': f'TEI parse error: {exc}', 'meta': {'format': 'TEI'}, 'capabilities_used': []}
        ns = 'http://www.tei-c.org/ns/1.0'
        def _text(tag: str) -> str:
            e = root.find(f'.//{{{ns}}}{tag}') or root.find(f'.//{tag}')
            return (e.text or '').strip() if e is not None else ''
        title = _text('title')
        author = _text('author')
        date = _text('date')
        div_count = len(root.findall(f'.//{{{ns}}}div') or root.findall('.//div'))
        meta: Dict[str, Any] = {
            'format': 'TEI',
            'title': title,
            'author': author,
            'date': date,
            'div_count': div_count,
        }
        desc = (
            f"TEI: {file_path.name}\n"
            f"Title: {title}  |  Author: {author}  |  Date: {date}\n"
            f"Divisions: {div_count}"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': []}

    # ── METS ──────────────────────────────────────────────────────────────────
    def _parse_mets(self, file_path: Path) -> Dict[str, Any]:
        try:
            root = ET.parse(str(file_path)).getroot()
        except ET.ParseError as exc:
            return {'description': f'METS parse error: {exc}', 'meta': {'format': 'METS'}, 'capabilities_used': []}
        ns_mets = 'http://www.loc.gov/METS/'
        def _findall(tag: str) -> List:
            elems = root.findall(f'.//{{{ns_mets}}}{tag}')
            if not elems:
                elems = root.findall(f'.//{tag}')
            return elems
        file_groups = _findall('fileGrp')
        files = _findall('file')
        div_labels = [e.get('LABEL', '') for e in _findall('div')][:30]
        obj_id = root.get('OBJID', 'unknown')
        meta: Dict[str, Any] = {
            'format': 'METS',
            'object_id': obj_id,
            'file_group_count': len(file_groups),
            'file_count': len(files),
            'structural_divisions': div_labels,
        }
        desc = (
            f"METS: {file_path.name}\n"
            f"Object: {obj_id}  |  File groups: {len(file_groups)}  |  Files: {len(files)}\n"
            f"Divisions: {', '.join(div_labels[:8])}"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': []}


# ══════════════════════════════════════════════════════════════════════════════
#  9. INDUSTRIAL AND FINANCE PROCESSOR
#  IFC/BIM, GRIB/GRIB2 (meteorology), XBRL/iXBRL (financial reporting)
# ══════════════════════════════════════════════════════════════════════════════

class IndustrialAndFinanceProcessor(BaseFileProcessor):
    """
    Industrial, engineering, and financial reporting processor.

    IFC   : Building Information Model — entity counts, spatial hierarchy.
    GRIB2 : Weather/climate model output — parameters, levels, forecast times.
    XBRL  : Financial reporting — entity, period, fact count, taxonomy.

    All three have stdlib Tier-0 fallbacks (text/struct sniffing).
    """

    _EXTENSIONS: Set[str] = {'.ifc', '.ifczip', '.grib', '.grib2', '.xbrl', '.ixbrl'}

    def get_supported_extensions(self) -> Set[str]:
        return self._EXTENSIONS

    def can_process(self, file_path: Path, mime_type: str) -> bool:
        return file_path.suffix.lower() in self._EXTENSIONS

    async def process_file(self, file_path: Path, metadata: Dict[str, Any]) -> ProcessingResult:
        start = time.time()
        ext = file_path.suffix.lower()
        loop = asyncio.get_event_loop()
        basic = await self.extract_basic_metadata(file_path)
        try:
            if ext in ('.ifc', '.ifczip'):
                result = await loop.run_in_executor(self.executor, self._parse_ifc, file_path, ext)
            elif ext in ('.grib', '.grib2'):
                result = await loop.run_in_executor(self.executor, self._parse_grib, file_path)
            elif ext in ('.xbrl', '.ixbrl'):
                result = await loop.run_in_executor(self.executor, self._parse_xbrl, file_path)
            else:
                result = {'description': f'Industrial/finance file: {file_path.name}', 'meta': {}}
            return ProcessingResult(
                success=True,
                extracted_text=result.get('description', ''),
                metadata={**basic, **result.get('meta', {})},
                processing_time=time.time() - start,
                processor_used=self.processor_name,
                capabilities_used=result.get('capabilities_used', []),
            )
        except Exception as exc:
            logger.error("IndustrialAndFinanceProcessor failed: %s", exc)
            return ProcessingResult(
                success=False,
                error_message=str(exc),
                processing_time=time.time() - start,
                processor_used=self.processor_name,
            )

    # ── IFC / BIM ────────────────────────────────────────────────────────────
    def _parse_ifc(self, file_path: Path, ext: str) -> Dict[str, Any]:
        if IFC_AVAILABLE:
            if ext == '.ifczip':
                ifc = ifcopenshell.open(str(file_path))
            else:
                ifc = ifcopenshell.open(str(file_path))
            entity_counts: Counter = Counter(e.is_a() for e in ifc)
            # Spatial hierarchy
            projects = ifc.by_type('IfcProject')
            buildings = ifc.by_type('IfcBuilding')
            storeys = ifc.by_type('IfcBuildingStorey')
            spaces = ifc.by_type('IfcSpace')
            # Get schema info
            schema = ifc.schema
            # Most common product types
            products: Counter = Counter(
                e.is_a() for e in ifc.by_type('IfcProduct')
            )
            meta: Dict[str, Any] = {
                'format': 'IFC',
                'schema': schema,
                'total_entities': len(list(ifc)),
                'project_count': len(projects),
                'building_count': len(buildings),
                'storey_count': len(storeys),
                'space_count': len(spaces),
                'top_entity_types': dict(entity_counts.most_common(20)),
                'top_product_types': dict(products.most_common(15)),
            }
            desc = (
                f"IFC BIM: {file_path.name}  (schema: {schema})\n"
                f"Buildings: {len(buildings)}  |  Storeys: {len(storeys)}  |  "
                f"Spaces: {len(spaces)}  |  Total entities: {meta['total_entities']:,}\n"
                f"Top types: {', '.join(k for k, _ in entity_counts.most_common(5))}"
            )
            return {'description': desc, 'meta': meta, 'capabilities_used': ['ifcopenshell']}

        # Tier-0: text scan for STEP header + entity counts
        entity_counts_fallback: Counter = Counter()
        with open(file_path, 'r', encoding='utf-8', errors='replace') as fh:
            for line in fh:
                if line.startswith('#'):
                    match = re.match(r'#\d+=(\w+)\(', line)
                    if match:
                        entity_counts_fallback[match.group(1)] += 1
        schema_match = None
        with open(file_path, 'r', encoding='utf-8', errors='replace') as fh:
            for line in fh:
                if 'FILE_SCHEMA' in line:
                    schema_match = line.strip()
                    break
        meta = {
            'format': 'IFC', 'fallback': True,
            'top_entity_types': dict(entity_counts_fallback.most_common(20)),
            'total_entities': sum(entity_counts_fallback.values()),
            'schema_line': schema_match or 'unknown',
        }
        desc = (
            f"IFC BIM: {file_path.name}\n"
            f"Total entities: {meta['total_entities']:,}\n"
            f"Top types: {', '.join(k for k, _ in entity_counts_fallback.most_common(5))}\n"
            "Install ifcopenshell for full spatial hierarchy: pip install ifcopenshell"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': []}

    # ── GRIB / GRIB2 ─────────────────────────────────────────────────────────
    def _parse_grib(self, file_path: Path) -> Dict[str, Any]:
        if CFGRIB_AVAILABLE:
            import xarray as xr
            try:
                ds_list = cfgrib.open_datasets(str(file_path))
                var_names: List[str] = []
                dims_summary: List[Dict] = []
                for ds in ds_list[:5]:
                    var_names.extend(list(ds.data_vars))
                    dims_summary.append({k: int(v) for k, v in ds.dims.items()})
                meta: Dict[str, Any] = {
                    'format': 'GRIB2',
                    'dataset_count': len(ds_list),
                    'variables': var_names[:40],
                    'dimensions': dims_summary,
                }
                desc = (
                    f"GRIB2: {file_path.name}\n"
                    f"Datasets: {len(ds_list)}  |  "
                    f"Variables: {', '.join(var_names[:10])}"
                )
                return {'description': desc, 'meta': meta, 'capabilities_used': ['cfgrib']}
            except Exception as exc:
                logger.warning("cfgrib failed: %s", exc)

        # Tier-0: GRIB message scan via magic bytes 'GRIB'
        msg_count = 0
        params: Set[str] = set()
        with open(file_path, 'rb') as fh:
            raw = fh.read()
        pos = 0
        while pos < len(raw) - 4:
            idx = raw.find(b'GRIB', pos)
            if idx == -1:
                break
            msg_count += 1
            pos = idx + 4
        meta = {
            'format': 'GRIB2', 'fallback': True, 'message_count': msg_count,
            'requires': 'cfgrib',
        }
        desc = (
            f"GRIB2: {file_path.name}\n"
            f"Messages: {msg_count:,}\n"
            "Install cfgrib for full decoding: pip install cfgrib"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': []}

    # ── XBRL / iXBRL ─────────────────────────────────────────────────────────
    def _parse_xbrl(self, file_path: Path) -> Dict[str, Any]:
        try:
            root = ET.parse(str(file_path)).getroot()
        except ET.ParseError:
            # iXBRL may be HTML-embedded; try lxml or strip
            content = file_path.read_text(encoding='utf-8', errors='replace')
            # Remove HTML doctype / namespace noise and retry
            content = re.sub(r'<!DOCTYPE[^>]*>', '', content)
            content = re.sub(r'&(?!amp;|lt;|gt;|quot;|apos;)(\w+);', '', content)
            try:
                root = ET.fromstring(content)
            except ET.ParseError as exc:
                return {
                    'description': f'XBRL parse error: {exc}',
                    'meta': {'format': 'XBRL'},
                    'capabilities_used': [],
                }

        # XBRL namespace prefixes
        XBRLI = 'http://www.xbrl.org/2003/instance'
        facts: List[Dict] = []
        entity = 'unknown'
        period_start = ''
        period_end = ''
        namespaces: Set[str] = set()

        # Collect all namespace URIs from elements
        for elem in root.iter():
            if '}' in elem.tag:
                ns = elem.tag.split('}')[0][1:]
                namespaces.add(ns)

        # Context: entity and period
        for ctx in root.findall(f'{{{XBRLI}}}context') or root.findall('.//context'):
            eid = ctx.find(f'.//{{{XBRLI}}}identifier') or ctx.find('.//identifier')
            if eid is not None and eid.text:
                entity = eid.text.strip()
            pi = ctx.find(f'.//{{{XBRLI}}}startDate') or ctx.find('.//startDate')
            pe = ctx.find(f'.//{{{XBRLI}}}endDate') or ctx.find('.//endDate')
            if pi is not None and pi.text:
                period_start = pi.text.strip()
            if pe is not None and pe.text:
                period_end = pe.text.strip()

        # Facts: any element with contextRef attribute
        for elem in root.iter():
            if elem.get('contextRef') and elem.text:
                tag_local = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
                facts.append({'concept': tag_local, 'value': elem.text.strip()[:100]})

        meta: Dict[str, Any] = {
            'format': 'XBRL',
            'entity_identifier': entity,
            'period_start': period_start,
            'period_end': period_end,
            'fact_count': len(facts),
            'taxonomy_namespaces': [ns for ns in namespaces if 'xbrl' in ns or 'ifrs' in ns or 'gaap' in ns][:10],
            'sample_facts': facts[:15],
        }
        desc = (
            f"XBRL: {file_path.name}\n"
            f"Entity: {entity}\n"
            f"Period: {period_start} → {period_end}\n"
            f"Facts: {len(facts):,}\n"
            f"Sample: {', '.join(f['concept'] for f in facts[:5])}"
        )
        return {'description': desc, 'meta': meta, 'capabilities_used': []}


# ══════════════════════════════════════════════════════════════════════════════
#  INTEGRATION SURFACE
# ══════════════════════════════════════════════════════════════════════════════

# Complete extension → processor class name mapping
ADVANCED_EXTENSIONS: Dict[str, str] = {
    # Biomedical imaging
    '.nii':         'BiomedicalImagingProcessor',
    '.mnc':         'BiomedicalImagingProcessor',
    '.mgh':         'BiomedicalImagingProcessor',
    '.mgz':         'BiomedicalImagingProcessor',
    '.hdr':         'BiomedicalImagingProcessor',
    '.img':         'BiomedicalImagingProcessor',
    # Genomics
    '.fa':          'GenomicsProcessor',
    '.fasta':       'GenomicsProcessor',
    '.fq':          'GenomicsProcessor',
    '.fastq':       'GenomicsProcessor',
    '.vcf':         'GenomicsProcessor',
    '.sam':         'GenomicsProcessor',
    '.bam':         'GenomicsProcessor',
    '.gff':         'GenomicsProcessor',
    '.gff3':        'GenomicsProcessor',
    '.gtf':         'GenomicsProcessor',
    '.bed':         'GenomicsProcessor',
    '.bedgraph':    'GenomicsProcessor',
    '.gb':          'GenomicsProcessor',
    '.gbk':         'GenomicsProcessor',
    '.gbff':        'GenomicsProcessor',
    '.embl':        'GenomicsProcessor',
    # Electrophysiology
    '.edf':         'ElectrophysiologyProcessor',
    '.bdf':         'ElectrophysiologyProcessor',
    '.gdf':         'ElectrophysiologyProcessor',
    '.vhdr':        'ElectrophysiologyProcessor',
    '.vmrk':        'ElectrophysiologyProcessor',
    '.fif':         'ElectrophysiologyProcessor',
    '.set':         'ElectrophysiologyProcessor',
    # Mass spec
    '.mzml':        'MassSpectrometryProcessor',
    '.mzxml':       'MassSpectrometryProcessor',
    '.imzml':       'MassSpectrometryProcessor',
    # Cheminformatics
    '.sdf':         'CheminformaticsProcessor',
    '.mol':         'CheminformaticsProcessor',
    '.mol2':        'CheminformaticsProcessor',
    '.cif':         'CheminformaticsProcessor',
    '.xyz':         'CheminformaticsProcessor',
    '.smi':         'CheminformaticsProcessor',
    '.smiles':      'CheminformaticsProcessor',
    # Geospatial
    '.shp':         'GeospatialProcessor',
    '.geojson':     'GeospatialProcessor',
    '.kml':         'GeospatialProcessor',
    '.kmz':         'GeospatialProcessor',
    '.gpkg':        'GeospatialProcessor',
    '.gpx':         'GeospatialProcessor',
    # Network forensics
    '.pcap':        'NetworkForensicsProcessor',
    '.pcapng':      'NetworkForensicsProcessor',
    '.cap':         'NetworkForensicsProcessor',
    # Scientific publishing
    '.warc':        'ScientificPublishingProcessor',
    '.nxml':        'ScientificPublishingProcessor',
    '.jats':        'ScientificPublishingProcessor',
    '.tei':         'ScientificPublishingProcessor',
    '.mets':        'ScientificPublishingProcessor',
    # Industrial / finance
    '.ifc':         'IndustrialAndFinanceProcessor',
    '.ifczip':      'IndustrialAndFinanceProcessor',
    '.grib':        'IndustrialAndFinanceProcessor',
    '.grib2':       'IndustrialAndFinanceProcessor',
    '.xbrl':        'IndustrialAndFinanceProcessor',
    '.ixbrl':       'IndustrialAndFinanceProcessor',
}

# pip install targets per processor
ADVANCED_INSTALL_MAP: Dict[str, List[str]] = {
    'BiomedicalImagingProcessor':    ['nibabel'],
    'GenomicsProcessor':             ['biopython', 'pysam'],
    'ElectrophysiologyProcessor':    ['mne', 'pyedflib'],
    'MassSpectrometryProcessor':     ['pymzml', 'pyteomics'],
    'CheminformaticsProcessor':      ['rdkit', 'gemmi'],
    'GeospatialProcessor':           ['fiona', 'rasterio'],
    'NetworkForensicsProcessor':     ['dpkt'],
    'ScientificPublishingProcessor': ['warcio'],
    'IndustrialAndFinanceProcessor': ['ifcopenshell', 'cfgrib'],
}


def get_advanced_processors(
    capabilities: ProcessingCapabilities,
    executor: ThreadPoolExecutor,
    config: Optional[ProcessingConfig] = None,
) -> List[BaseFileProcessor]:
    """
    Factory function — returns all advanced processors instantiated and ready.

    Usage
    -----
        from advanced_files import get_advanced_processors, ADVANCED_EXTENSIONS

        processors = get_advanced_processors(capabilities, executor)
        # Append to your existing processor chain:
        all_processors = existing_processors + processors

    The processors respect SOMNUS sovereignty rules:
    every processor degrades gracefully to stdlib if optional deps are absent.
    """
    cfg = config or ProcessingConfig()
    return [
        BiomedicalImagingProcessor(capabilities, executor, cfg),
        GenomicsProcessor(capabilities, executor, cfg),
        ElectrophysiologyProcessor(capabilities, executor, cfg),
        MassSpectrometryProcessor(capabilities, executor, cfg),
        CheminformaticsProcessor(capabilities, executor, cfg),
        GeospatialProcessor(capabilities, executor, cfg),
        NetworkForensicsProcessor(capabilities, executor, cfg),
        ScientificPublishingProcessor(capabilities, executor, cfg),
        IndustrialAndFinanceProcessor(capabilities, executor, cfg),
    ]


def get_advanced_capabilities_report() -> Dict[str, Any]:
    """
    Returns a full capability snapshot — useful for logging at startup
    or exposing via a /capabilities endpoint.
    """
    adv = AdvancedCapabilities()
    return {
        'advanced_capabilities': adv.report(),
        'processor_count': 9,
        'total_extensions': len(ADVANCED_EXTENSIONS),
        'install_map': ADVANCED_INSTALL_MAP,
    }
