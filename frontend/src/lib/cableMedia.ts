/**
 * cableMedia — derives the physical cable media a link *should* use from the
 * port types of the two interfaces it connects (keputusan Surya 2026-09-07:
 * "sesuaikan dengan jenis perangkat", not a user-picked or hardcoded media).
 *
 * Deliberately separate from rack3d's `mediaFor()`: that one picks a media
 * for two already-rack-mounted `DeviceDef`s using their 3D port geometry.
 * This one only needs the two `IfaceType`s and works before either device
 * has ever touched a rack — the only shape `TopologyCanvas`'s onConnect has
 * on hand.
 */
import type { CableMedia, IfaceType } from '@/api/types';

/** Each port type's own natural media, were both ends the same type. */
const MEDIA_BY_IFACE: Record<Exclude<IfaceType, 'wifi'>, CableMedia> = {
  eth: 'cat6', // RJ45 copper — same default CableCreate.media already uses
  sfp: 'mmf_om3', // 1G/10G SFP optics are almost always short-reach multimode
  sfp28: 'mmf_om3', // 10/25G SFP+/SFP28 cage, same multimode family
  qsfp: 'mmf_om4', // 40/100G needs the higher-bandwidth fiber grade
  gpon: 'gpon_drop', // dedicated PON drop fiber, the only sensible media for a GPON port
};

/** When the two ends disagree, the more optically/electrically constrained
 *  connector wins: a copper patch physically cannot terminate on an SFP
 *  cage, so any optical type beats plain `eth`, and among optical cages the
 *  higher-capacity one wins. Listed most- to least-constrained. */
const PRIORITY: Exclude<IfaceType, 'wifi'>[] = ['gpon', 'qsfp', 'sfp28', 'sfp', 'eth'];

/**
 * Returns the `CableMedia` a physical cable between these two port types
 * should use, or `null` when no cable is sensible at all.
 *
 * `wifi` never gets a cable — a radio link has no physical run to model
 * (keputusan Surya 2026-09-07, explicit follow-on from "sesuaikan dengan
 * jenis perangkat": a wireless port's "natural" media is no media).
 */
export function mediaForIfaceTypes(a: IfaceType, b: IfaceType): CableMedia | null {
  if (a === 'wifi' || b === 'wifi') return null;
  const winner = PRIORITY.indexOf(a) <= PRIORITY.indexOf(b) ? a : b;
  return MEDIA_BY_IFACE[winner];
}
