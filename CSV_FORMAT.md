# Pressure Map CSV Format

This document describes the CSV format expected by the Pressure Monitoring dashboard for pressure map data.

## Overview

Each CSV file represents **one pressure map frame** — a 2D grid of pressure values from a sensor mat. Values are typically in arbitrary pressure units (e.g. 0–200).

## Supported Formats

### Format 1: Simple grid (no header)

Comma-separated numeric values. Each row = one row of the pressure grid.

```
10,12,15,18,22,25,28,30,28,25,22,18,15,12,10
12,15,20,25,32,38,42,45,42,38,32,25,20,15,12
15,20,28,35,45,55,62,68,62,55,45,35,28,20,15
...
```

- **Example**: `sample_pressure_1.csv`
- All rows must have the same number of columns
- Empty rows are skipped

### Format 2: With optional header

If the first row contains non-numeric values (e.g. column labels), it is skipped.

```
col0,col1,col2,col3,...
10,12,15,18,...
12,15,20,25,...
...
```

- **Example**: `sample_pressure_2.csv`

### Format 3: With timestamp column (optional)

If the first column looks like a datetime, it is treated as a per-row timestamp and skipped when building the grid. **Note**: With `has_header=True` (default), a non-numeric first cell causes the first row to be skipped as a header, so timestamp format may lose the first data row. Use simple numeric grids for reliable imports.

## Data Requirements

| Requirement | Description |
|-------------|-------------|
| **Values** | Numeric (integers or decimals) |
| **Encoding** | UTF-8 (BOM optional) |
| **Separator** | Comma (`,`) |
| **Grid shape** | Rectangular; all rows same length |

## Analysis Thresholds (from code)

- **Lower threshold**: 5.0 — pixels below this are not counted as "contact"
- **High pressure**: 80.0 — values above this may trigger alerts
- **PPI minimum**: 10 pixels — regions smaller than this are excluded from Peak Pressure Index

## Import Methods

1. **Web upload**: Patient or clinician uploads via "Upload CSV"
2. **Management command**:
   ```bash
   python manage.py import_pressure_csv <username> <path_to.csv> [session_name]
   ```
   Example:
   ```bash
   python manage.py import_pressure_csv patient1 sample_data\sample_pressure_1.csv "Day 1"
   ```
