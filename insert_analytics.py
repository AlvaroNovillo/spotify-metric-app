# Read both files
with open('app/templates/_artist_display.html', 'r', encoding='utf-8') as f:
    artist_display = f.read()

with open('analytics_sections.html', 'r', encoding='utf-8') as f:
    analytics = f.read()

# Find the insertion point (after the genres/tags section, before top tracks)
# Look for the closing div of the genres section
insertion_marker = '</div>\n\n{# --- Top Tracks & Release Stats --- #}'

if insertion_marker in artist_display:
    # Insert analytics sections
    artist_display = artist_display.replace(
        insertion_marker,
        '</div>\n\n' + analytics + '\n\n{# --- Top Tracks & Release Stats --- #}'
    )
    
    # Write back
    with open('app/templates/_artist_display.html', 'w', encoding='utf-8') as f:
        f.write(artist_display)
    
    print("Successfully added analytics sections to _artist_display.html")
else:
    print("ERROR: Could not find insertion point")
    print("Trying alternative marker...")
    
    # Try alternative insertion point
    alt_marker = '{# --- Top Tracks & Release Stats --- #}'
    if alt_marker in artist_display:
        artist_display = artist_display.replace(
            alt_marker,
            analytics + '\n\n{# --- Top Tracks & Release Stats --- #}'
        )
        
        with open('app/templates/_artist_display.html', 'w', encoding='utf-8') as f:
            f.write(artist_display)
        
        print("Successfully added analytics sections using alternative marker")
    else:
        print("ERROR: Could not find any suitable insertion point")
