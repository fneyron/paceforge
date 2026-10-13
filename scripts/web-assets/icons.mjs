// Only the icons used by data-lucide in the server templates.
import { createIcons, Activity, Bike, Footprints, GitCompare, LogOut, Mountain, RefreshCw, Waves } from 'lucide';
const icons = { Activity, Bike, Footprints, GitCompare, LogOut, Mountain, RefreshCw, Waves };
window.lucide = { createIcons: () => createIcons({ icons }) };
