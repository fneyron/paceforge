import * as Calendar from 'expo-calendar';
import { Linking } from 'react-native';
import { HORIZON_DAYS, MAX_ATTENDEE_READS, OWN_CALENDAR, PhoneEvent } from './phone-calendar';

// The iPhone's calendars read with EventKit (expo-calendar's API of SDK 57), for « Mon agenda ». What is read stays on
// the phone: titles, places, notes, attendees and calendar names are looked at only to apply the rules (the feed's own
// calendar is skipped by name, a declined invitation by the owner's answer) and are never kept nor sent. Never logged.

export type Access = 'granted' | 'denied' | 'undetermined';

/** Full access to the calendars (iOS 17+: NSCalendarsFullAccessUsageDescription; reading needs more than write-only).
 * `ask`: show the system prompt when it has never been answered. */
export async function calendarAccess(ask: boolean): Promise<Access> {
  const current = await Calendar.getCalendarPermissions();
  if (current.granted) return 'granted';
  if (!ask) return current.status === 'undetermined' ? 'undetermined' : 'denied';
  if (!current.canAskAgain) return 'denied';
  const answer = await Calendar.requestCalendarPermissions();
  return answer.granted ? 'granted' : 'denied';
}

/** The events of the next HORIZON_DAYS days in every calendar of the phone but PaceForge's own feed, reduced at once to
 * the fields the rules read. */
export async function readPhoneEvents(now: Date): Promise<PhoneEvent[]> {
  const until = new Date(now.getTime() + HORIZON_DAYS * 86400000);
  const calendars = (await Calendar.getCalendars(Calendar.EntityTypes.EVENT)).filter(c => c.title !== OWN_CALENDAR);
  if (!calendars.length) return [];
  const events = await Calendar.listEvents(calendars, now, until);
  let checks = 0;
  const out: PhoneEvent[] = [];
  for (const e of events) {
    let declined = false;
    // an invitation (it has an organizer): the owner's own answer, read on the phone; past the bound, counted busy
    if ((e as { organizer?: unknown }).organizer && checks < MAX_ATTENDEE_READS) {
      checks++;
      try { declined = (await e.getAttendees()).some(a => a.isCurrentUser && a.status === Calendar.AttendeeStatus.DECLINED); }
      catch { /* unreadable: counted busy */ }
    }
    out.push({ startDate: e.startDate, endDate: e.endDate, allDay: !!e.allDay, availability: e.availability ?? null,
      status: e.status ?? null, declined });
  }
  return out;
}

/** The app's page in the phone's settings, where its access to the calendars is given back. */
export function openPhoneSettings(): Promise<void> {
  return Linking.openSettings();
}
