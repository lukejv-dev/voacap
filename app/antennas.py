"""Fixed catalog of antenna pattern files bundled under itshfbc/antennas/samples/
(v1 scope: pick from this list rather than uploading custom antenna files)."""

# (sample_file, description) - descriptions taken from each file's own header line
ANTENNA_CATALOG = [
    ("SAMPLE.00", "Constant gain isotrope"),
    ("SAMPLE.01", "Multiband Aperiodic Reflector Array"),
    ("SAMPLE.02", "Dual-Band Center-Fed Half-Wave Dipole Array"),
    ("SAMPLE.03", "Dual-Band End-Fed Half-Wave Dipole Array"),
    ("SAMPLE.04", "Tropical Array"),
    ("SAMPLE.05", "Horizontal Log-Periodic"),
    ("SAMPLE.06", "Vertical Log-Periodic"),
    ("SAMPLE.07", "Horizontal Rhombic"),
    ("SAMPLE.08", "Quadrant Antenna"),
    ("SAMPLE.09", "Crossed-Dipole Antenna"),
    ("SAMPLE.10", "Vertical Monopole"),
    ("SAMPLE.21", "ITSA-1 Terminated Horizontal Rhombic"),
    ("SAMPLE.22", "ITSA-1 Vertical Monopole"),
    ("SAMPLE.23", "ITSA-1 Horizontal Dipole"),
    ("SAMPLE.24", "ITSA-1 Horizontal Yagi"),
    ("SAMPLE.25", "ITSA-1 Vertical Log-Periodic"),
    ("SAMPLE.26", "ITSA-1 Curtain"),
    ("SAMPLE.27", "ITSA-1 Sloping Vee"),
    ("SAMPLE.28", "ITSA-1 Inverted L"),
    ("SAMPLE.32", "ITS-78 Vertical Monopole"),
    ("SAMPLE.34", "ITS-78 Horizontal Yagi"),
    ("SAMPLE.35", "ITS-78 Vertical Dipole"),
    ("SAMPLE.36", "ITS-78 Curtain"),
    ("SAMPLE.48", "NOSC Inverted Cone antenna"),
]

DEFAULT_ANTENNA = "SAMPLE.23"
