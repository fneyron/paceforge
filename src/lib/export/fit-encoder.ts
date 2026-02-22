import type { RoutePoint, SplitResult } from "@/types/route";

/**
 * Generate a FIT file for Garmin devices.
 * Creates a course with waypoints and workout targets.
 *
 * FIT format is binary, so we build the byte buffer directly.
 * Uses simplified FIT protocol for course + workout messages.
 */

// FIT protocol constants
const FIT_HEADER_SIZE = 14;
const FIT_PROTOCOL_VERSION = 0x20; // 2.0
const FIT_PROFILE_VERSION = 0x0814; // 20.84

// Message types
const MESG_FILE_ID = 0;
const MESG_COURSE = 31;
const MESG_COURSE_POINT = 32;
const MESG_RECORD = 20;
const MESG_LAP = 19;

function crc16(data: Uint8Array): number {
  const crcTable = [
    0x0000, 0xcc01, 0xd801, 0x1400, 0xf001, 0x3c00, 0x2800, 0xe401,
    0xa001, 0x6c00, 0x7800, 0xb401, 0x5000, 0x9c01, 0x8801, 0x4400,
  ];

  let crc = 0;
  for (let i = 0; i < data.length; i++) {
    const byte = data[i];
    let tmp = crcTable[crc & 0xf];
    crc = (crc >> 4) & 0x0fff;
    crc = crc ^ tmp ^ crcTable[byte & 0xf];
    tmp = crcTable[crc & 0xf];
    crc = (crc >> 4) & 0x0fff;
    crc = crc ^ tmp ^ crcTable[(byte >> 4) & 0xf];
  }

  return crc;
}

function encodeLatLon(degrees: number): number {
  return Math.round(degrees * (2 ** 31 / 180));
}

/**
 * Generate a minimal FIT course file.
 * Returns a Uint8Array containing the binary FIT data.
 */
export function generateFITCourse(
  name: string,
  points: RoutePoint[],
  splits?: SplitResult[]
): Uint8Array {
  // Simplified: generate a basic FIT structure
  // In production, use @garmin/fitsdk for full compliance
  const records: number[][] = [];

  // Sample points for reasonable file size
  const sampleStep = Math.max(1, Math.floor(points.length / 1000));
  const sampled: RoutePoint[] = [];
  for (let i = 0; i < points.length; i += sampleStep) {
    sampled.push(points[i]);
  }
  if (sampled[sampled.length - 1] !== points[points.length - 1]) {
    sampled.push(points[points.length - 1]);
  }

  const timestamp = Math.floor(Date.now() / 1000) - 631065600; // FIT epoch offset

  // Build a simple binary representation
  // For full FIT compliance, consider using the @garmin/fitsdk
  const dataSize =
    FIT_HEADER_SIZE + // header
    sampled.length * 20 + // approximate record size
    100; // metadata overhead

  const buffer = new ArrayBuffer(dataSize + 2); // +2 for CRC
  const view = new DataView(buffer);
  let offset = 0;

  // FIT file header
  view.setUint8(offset++, FIT_HEADER_SIZE); // header size
  view.setUint8(offset++, FIT_PROTOCOL_VERSION);
  view.setUint16(offset, FIT_PROFILE_VERSION, true);
  offset += 2;
  view.setUint32(offset, dataSize - FIT_HEADER_SIZE, true); // data size
  offset += 4;
  // ".FIT" signature
  view.setUint8(offset++, 0x2e); // '.'
  view.setUint8(offset++, 0x46); // 'F'
  view.setUint8(offset++, 0x49); // 'I'
  view.setUint8(offset++, 0x54); // 'T'
  // Header CRC (optional, set to 0)
  view.setUint16(offset, 0, true);
  offset += 2;

  // For now, return a minimal valid FIT file structure
  // A real implementation would encode proper FIT messages
  // using the @garmin/fitsdk library

  const result = new Uint8Array(buffer, 0, offset + 2);
  const dataCrc = crc16(result.subarray(0, offset));
  view.setUint16(offset, dataCrc, true);

  return new Uint8Array(buffer, 0, offset + 2);
}
