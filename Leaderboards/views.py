import re
from re import RegexFlag as ref # Specifically to avoid a PyDev Error in the IDE. 
import json
import pytz
import sys
import cProfile, pstats, io
from datetime import datetime, date, timedelta

#from collections import OrderedDict

from django_generic_view_extensions.views import TemplateViewExtended, DetailViewExtended, DeleteViewExtended, CreateViewExtended, UpdateViewExtended, ListViewExtended
from django_generic_view_extensions.util import  datetime_format_python_to_PHP, class_from_string
from django_generic_view_extensions.options import  list_display_format, object_display_format
from django_generic_view_extensions.debug import print_debug 

from Leaderboards.models import Team, Player, Game, League, Location, Session, Rank, Performance, Rating, ALL_LEAGUES, ALL_PLAYERS, ALL_GAMES, NEVER
#from django import forms
from django.db.models import Count, Q
#from django.db.models.fields import DateField 
from django.shortcuts import render
from django.utils import timezone
from django.http import HttpResponse
from django.urls import reverse #, resolve
from django.contrib.auth.models import Group
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.serializers.json import DjangoJSONEncoder
from django.conf.global_settings import DATETIME_INPUT_FORMATS
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_aware, make_aware

# TODO: Fix timezone handling. By default in Django we use UTC but we want to enter sessons in local time and see results in local time.
#        This may need to be League hooked and/or Venue hooked, that is leagues specify a timezone and Venues can specfy one that overrides?
#        Either way when adding a session we don't know until the League and Venue are chosen what timezone to use.
#        Requires a postback on a League or Venue change? So we can render the DateTime and read box in the approriate timezone?
from django.utils.timezone import get_default_timezone_name, get_current_timezone_name
from django.utils.formats import localize

#TODO: Add account security, and test it
#TODO: Once account security is in place a player will be in certain leagues, restrict some views to info related to those leagues.
#TODO: Put a filter in the menu bar, for selecting a league, and then restrict a lot of views only to that league's data.

#TODO: Add testing: https://docs.djangoproject.com/en/1.10/topics/testing/tools/

#===============================================================================
# Some support routines
#===============================================================================

def get_aware_datetime(date_str):
    ret = parse_datetime(date_str)
    if not is_aware(ret):
        ret = make_aware(ret)
    return ret

def is_registrar(user):
    return user.groups.filter(name='registrars').exists()

def fix_time_zone(dt):
    UTC = pytz.timezone('UTC')
    if not dt is None and dt.tzinfo == None:
        return UTC.localize(dt)
    else:
        return dt

#===============================================================================
# Form processing specific to CoGs
#===============================================================================

def updated_user_from_form(user, request):
    '''
    Updates a user object in the database (from the Django auth module) with information from the submitted form, specific to CoGs
    '''
    POST = request.POST
    registrars = Group.objects.get(name='registrars')

    user.username = POST['name_nickname']       # TODO: nicknames can have spaces, does the auth model support usernames with spaces? 
    user.first_name = POST['name_personal']
    user.last_name = POST['name_family']
    user.email = POST['email_address']
    user.is_staff = 'is_staff' in POST
    if 'is_registrar' in POST:
        if not is_registrar(user):
            user.groups.add(registrars)
    else:
        if is_registrar(user):
            registrars.user_set.remove(user)
    user.save

def clean_submitted_data(self):
    '''
    Do stuff before the model is saved
    '''
    model = self.model._meta.model_name

def pre_process_submitted_model(self):
    pass
    
def post_process_submitted_model(self):
    '''
    When a model form is posted, this function will perform model specific updates, based on the model 
    specified in the form (kwargs) as "model"
    
    It will be running inside a transaction and can bail with an IntegrityError if something goes wrong 
    achieving a rollback.
    
    This is executed inside a transaction as a callback from generic_view_extensions CreateViewExtended and UpdateViewExtended,
    so it can throw an Integrity Error to roll back the transaction. This is important if it is trying to update a number of 
    models at the same time that are all related. The integrity of relations after the save should be tested and if not passed,
    then throw an IntegrityError.   
    '''
    model = self.model._meta.model_name

    if model == 'player':
        pass # updated_user_from_form(...) # TODO: Need when saving users update the auth model too.
    elif model == 'session':
    # TODO: When saving sessions, need to do a confirmation step first, reporting the impacts.
    #       Editing a session will have to force recalculation of all the rating impacts of sessions
    #       all the participating players were in that were played after the edited session.
    #    A general are you sure? system for edits is worth implementing.
    
    # TODO: When saving a session sort ranks numerically and substitute by 1, 2, 3, 4 ... to ensure victors are always identifes by rank 1 and rank is the ordinal.
    #        Silently enforcing this is better than requiring the user to. The form can support any integers that indicate order.

        session = self.object
        
        # TODO: Bug here?
        # session.ranks for some reason is None
        # even after a session.reload_from_db
        # So can fetch the ranks explicitly. 
        # But something with the Django field "ranks" is broken
        # And not sure if it's a local issue or a bug in Django.
        #
        # If we sort them in the order that they were created then we have 
        # a list that is the same order as the TeamPlayers list which we 
        # we build below, because all are created in order of the submitted
        # form fields.
        #
        # We can sort on pk or creeated_on for the same expected result.
        ranks = Rank.objects.filter(session=session.pk).order_by('created_on')
        
        # TODO: Sort Ranks, and map onto a list of 1, 2, 3, 4 ... 
        
        team_play = session.team_play

        if team_play:
            # Get the player list for submitted teams and the name.
            # Find a team with those players
            # If the name is not blank or Team n then update the team name
            # If it doesn't exist, create a team and give it the specified name or null of Team n

            # Work out the total number of players and initialize a TeamPlayers and teamRank records
            num_teams = int(self.request.POST["num_teams"])
            num_players = 0
            TeamPlayers = []
            for t in range(num_teams):
                num_team_players = int(self.request.POST["Team-%d-num_players" % t])
                num_players += num_team_players
                TeamPlayers.append([])

            # Populate the TeamPlayers record (i.e. work out which players are on the same team)
            for p in range(num_players):
                player = int(self.request.POST["Performance-%d-player" % p])
                team_num = int(self.request.POST["Performance-%d-team_num" % p])
                TeamPlayers[team_num].append(player)

            # For each team now, find it, create it , fix it as needed and associate it with the appropriate Rank just created
            for t in range(num_teams):
                # Rank objects were saved in the database in the order of the 
                # TeamRanks list. The TeamPlayers list has a matching list of  
                # player Ids. t is stepping through these lists. So the rank object 
                # we want to attach this team to is simply: 
                rank = ranks[t]

                # Similarly the team players list is in the same order
                team_players = TeamPlayers[t]

                # The name submitted for this team 
                new_name = self.request.POST["Team-%d-name" % t]

                # Find the team object that has these specific players.
                # Filter by count first and filter by players one by one.
                teams = Team.objects.annotate(count=Count('players')).filter(count=len(team_players))
                for player in team_players:
                    teams = teams.filter(players=player)

                # If not found, then create a team object with those players and 
                # link it to the rank object
                if len(teams) == 0:
                    team = Team.objects.create()

                    for player_id in team_players:
                        player = Player.objects.get(id=player_id)
                        team.players.add(player)

                    if new_name and not re.match("^Team \d+$", new_name, ref.IGNORECASE):
                        team.name = new_name
                        team.save()

                    rank.team=team
                    rank.save()

                # If one is found, then link it to the approriate rank object and 
                # check its name against the submission (updating if need be)
                elif len(teams) == 1:
                    team = teams[0]

                    if new_name and not re.match("^Team \d+$", new_name, ref.IGNORECASE) and new_name != team.name :
                        team.name = new_name
                        team.save()

                    if (rank.team != team):
                        rank.team=team
                        rank.save()

                # Weirdness, we can't legally have more than one team with the same set of players in the database
                else:
                    raise ValueError("Database error: More than one team with same players in database.")

        # TODO: Ensure each player in game is unique.
        
        # Calculate and save the TrueSkill rating impacts to the Performance records
        session.calculate_trueskill_impacts()
        
        # Then update the ratings for all players of this game
        Rating.update(session)
        
        # Now check the integrity of the save. For a sessions, this means that:
        #
        # If it is a team_play session:
        #    The game supports team_play
        #    The ranks all record teams and not players
        #    There is one performance object for each player (accessed through Team).
        # If it is not team_play:
        #    The game supports individua_play
        #    The ranks all record players not teams
        #    There is one performance record for each player/rank
        # The before trueskill values are identical to the after trueskill values for each players 
        #     prior session on same game.
        # The rating for each player at this game has a playcount that is one higher than it was!
        # The rating has recorded the global trueskill settings and the Game trueskill settings reliably.
        #
        # TODO: Do these checks. Then do test of the transaction rollback and error catch by 
        #       simulating an integrity error.  

def html_league_options():
    '''
    Returns a simple string of HTML OPTION tags for use in a SELECT tag in a template
    '''
    leagues = League.objects.all()
    
    options = ['<option value="0">Global</option>']  # Reserved ID for global (no league selected).    
    for league in leagues:
        options.append('<option value="{}">{}</option>'.format(league.id, league.name))
    return "\n".join(options)

def extra_context_provider(self):
    '''
    Returns a dictionary for extra_context with CoGs specific items 
    
    Specifically The session form when editing existing sessions has a game already known,
    and this game has some key properties that the form wants to know about. Namely:
    
    individual_play: does this game permit individual play
    team_play: does this game support team play
    min_players: minimum number of players for this game
    max_players: maximum number of players for this game
    min_players_per_team: minimum number of players in a team in this game. Relevant only if team_play supported.
    max_players_per_team: maximum number of players in a team in this game. Relevant only if team_play supported.
    
    Clearly altering the game should trigger a reload of this metadata for the newly selected game.
    See ajax_Game_Properties below for that. 
    '''
    context = {}
    model = getattr(self, "model", None)
    model_name = model._meta.model_name if model else ""
     
    context['league_options'] = html_league_options()
    
    if model_name == 'session' and hasattr(self, "object"):
        Default = Game()
        context['game_individual_play'] = json.dumps(Default.individual_play)
        context['game_team_play'] = json.dumps(Default.team_play)
        context['game_min_players'] = Default.min_players
        context['game_max_players'] = Default.max_players
        context['game_min_players_per_team'] = Default.min_players_per_team
        context['game_max_players_per_team'] = Default.max_players_per_team
        
        session = self.object
                
        if session:
            game = session.game
            if game:
                context['game_individual_play'] = json.dumps(game.individual_play) # Python True/False, JS true/false 
                context['game_team_play'] = json.dumps(game.team_play)
                context['game_min_players'] = game.min_players
                context['game_max_players'] = game.max_players
                context['game_min_players_per_team'] = game.min_players_per_team
                context['game_max_players_per_team'] = game.max_players_per_team
    
    return context

#===============================================================================
# Customize Generic Views for CoGs
#===============================================================================
# TODO: Test that this does validation and what it does on submission errors

class view_Home(TemplateViewExtended):
    template_name = 'CoGs/view_home.html'
    extra_context_provider = extra_context_provider

class view_Add(LoginRequiredMixin, CreateViewExtended):
    # TODO: Should be atomic with an integrity check on all session, rank, performance, team, player relations.
    template_name = 'CoGs/form_data.html'
    operation = 'add'
    extra_context_provider = extra_context_provider
    #pre_processor = clean_submitted_data
    pre_processor = pre_process_submitted_model
    post_processor = post_process_submitted_model

# TODO: Test that this does validation and what it does on submission errors

class view_Edit(LoginRequiredMixin, UpdateViewExtended):
    # TODO: Must be atomic and in such a way that it tests if changes haveintegrity.
    #       notably if a session changes from indiv to team mode say or vice versa,
    #       there is a notable impact on rank objects that could go wrong and we should
    #       check integrity. 
    #       Throw:
    #        https://docs.djangoproject.com/en/1.10/ref/exceptions/#django.db.IntegrityError
    #       if an integrity error is found in such a transaction (or any transaction).
    template_name = 'CoGs/form_data.html'
    operation = 'edit'
    extra_context_provider = extra_context_provider
    #pre_processor = clean_submitted_data
    pre_processor = pre_process_submitted_model
    post_processor = post_process_submitted_model

class view_Delete(LoginRequiredMixin, DeleteViewExtended):
    # TODO: Should be atomic for sesssions as a session delete needs us to delete session, ranks and performances
    # TODO: When deleting a session need to check for ratings that refer to it as last_play or last_win
    #        and fix the reference or delete the rating.
    template_name = 'CoGs/delete_data.html'
    operation = 'delete'
    format = object_display_format()
    extra_context_provider = extra_context_provider

class view_List(ListViewExtended):
    template_name = 'CoGs/list_data.html'
    operation = 'list'
    format = list_display_format()
    extra_context_provider = extra_context_provider

class view_Detail(DetailViewExtended):
    template_name = 'CoGs/view_data.html'
    operation = 'view'
    format = object_display_format()
    extra_context_provider = extra_context_provider

#===============================================================================
# The Leaderboards view. What it's all about!
#===============================================================================

# Define defaults for the view inputs 

class leaderboard_options:
    league = ALL_LEAGUES    # Restrict to games played by specified League
    player = ALL_PLAYERS    # Restrict to games played by specified Player
    game = ALL_GAMES        # Restrict to specified Game
    num_games = 5           # List only this many games (most popular ones)
    num_days = 1            # For impact Quick Views only, return impact of last session of this length in days
    as_at = None            # Do everything as if it were this time now (pretend it is now as_at)
    changed_since = NEVER   # Show only leaderboards that changed since this date 
    compare_with = None     # Compare with this many historic leaderboards
    compare_back_to = None  # Compare all leaderboards back to this date (and the leaderboard that was he latest one then)
    compare_till = None     # Include comparisons only up to this date           
    highlight = True        # Highlight changes between historic snapshots
    details = False         # Show session details atop each boards (about the session that produced that board) 
    cols = 4                # Display boards in this many columns (ignored when comparing with historic boards)
    names = 'complete'      # Render player names like this
    links = 'CoGs'          # Link games and players to this target
    
    @property
    def as_dict(self):
        me = sys._getframe().f_code.co_name
        d = {}
        for attr in [a for a in dir(self) if not a.startswith('__')]:
            if attr != me:
                val = getattr(self, attr)
                if isinstance(val, datetime):
                    val = val.strftime(DATETIME_INPUT_FORMATS[0])
                elif not isinstance(val, str):
                    try:
                        val = json.dumps(val)
                    except TypeError:
                        val = str(val)
                
                d[attr] = val
                
        return d
    
def get_leaderboard_options(request):
    lo = leaderboard_options()
    if 'league' in request.GET:
        lo.league = request.GET['league']
        
    if 'player' in request.GET:
        lo.player = request.GET['player']
        
    if 'game' in request.GET:        
        lo.game = request.GET['game']
    
    if 'num_games' in request.GET and request.GET['num_games'].isdigit():
        lo.num_games = int(request.GET["num_games"])

    if 'num_days' in request.GET and request.GET['num_days'].isdigit():
        lo.num_days = int(request.GET["num_days"])

    if 'as_at' in request.GET:
        lo.as_at = fix_time_zone(parser.parse(request.GET['as_at']))
                                 
    if 'changed_since' in request.GET:
        lo.changed_since = fix_time_zone(parser.parse(request.GET['changed_since']))

    if 'compare_with' in request.GET and request.GET['compare_with'].isdigit():
        lo.compare_with = int(request.GET['compare_with'])

    if 'compare_back_to' in request.GET:                                         
        lo.compare_back_to = fix_time_zone(parser.parse(request.GET['compare_back_to']))

    if 'compare_till' in request.GET:        
        lo.compare_till = fix_time_zone(parser.parse(request.GET['compare_till']))    
        
    if 'highlight' in request.GET:
        lo.highlight = json.loads(request.GET['highlight'].lower())
         
    if 'details' in request.GET:
        lo.details = json.loads(request.GET['details'].lower())     
    
    if 'cols' in request.GET:
        lo.cols = request.GET['cols']
        
    if 'names' in request.GET:
        lo.names = request.GET['names']
    
    if 'links' in request.GET: 
        lo.links = request.GET['links']
    
#     # TODO: Bail with an error message
#     # TODO: Do consistency checks on all the dates submitted
#     if league != ALL_LEAGUES and not League.objects.filter(pk=league).exists():
#         pass
#     if player != ALL_PLAYERS and not Player.objects.filter(pk=player).exists():
#         pass
#     if game != ALL_GAMES and not Game.objects.filter(pk=game).exists():
#         pass    
    
    return lo

def get_leaderboard_titles(lo):
    # TODO: Make this more robust against illegal PKs. The whole view should be graceful if sent a bad league or player.
    try:
        P = Player.objects.get(pk=lo.player)
    except:
        P = None

    try:
        L = League.objects.get(pk=lo.league)
    except:
        L = None
        
    # Format the page title
    if lo.player == ALL_PLAYERS:
        if lo.league == ALL_LEAGUES:
            title = "Global Leaderboards"
        else:
            title = "Leaderboards for the {} league".format(L)
    else:
        if lo.league == ALL_LEAGUES:
            title = "Leaderboards for {}".format(P)
        else:
            title = "Leaderboards for {} in the {} league".format(P, L)        

    default = leaderboard_options()
    
    subtitle = []
    if lo.as_at != default.as_at:
        subtitle.append("as at {}".format(localize(lo.as_at)))

    if lo.changed_since != default.changed_since:
        subtitle.append("changed after {}".format(localize(lo.changed_since)))

    if lo.compare_back_to != default.compare_back_to:
        time = "that same time" if lo.compare_back_to == lo.changed_since else localize(lo.compare_back_to)
        subtitle.append("compared back to the leaderboard as at {}".format(time))
    elif lo.compare_with != default.compare_with:
        subtitle.append("compared up to with {} prior leaderboards".format(lo.compare_with))

    if lo.compare_till != default.compare_till:
        subtitle.append("compared up to the leaderboard as at {}".format(localize(lo.compare_till)))
        
    return (title, "<BR>".join(subtitle))

def view_Leaderboards(request): 
    '''
    The raison d'etre of the whole site, this view presents the leaderboards. 
    '''
    # Fetch the leaderboards
    leaderboards = ajax_Leaderboards(request, raw=True)   

    lo = get_leaderboard_options(request)
    
    default = leaderboard_options()
    
    (title, subtitle) = get_leaderboard_titles(lo)

    # Get list of leagues, (pk, name) tuples.
    leagues = [(ALL_LEAGUES, 'ALL')] 
    leagues += [(x.pk, str(x)) for x in League.objects.all()]

    # Get list of players, (pk, name) tuples.
    players = [(ALL_PLAYERS, 'ALL')] 
    if lo.league == ALL_LEAGUES:
        players += [(x.pk, str(x)) for x in Player.objects.all()]    
    else:   
        players += [(x.pk, str(x)) for x in Player.objects.filter(leagues__pk=lo.league)]

    # Get list of games, (pk, name) tuples.
    games = [(ALL_GAMES, 'ALL')] 
    if lo.league == ALL_LEAGUES:
        games += [(x.pk, str(x)) for x in Game.objects.all()]    
    else:   
        games += [(x.pk, str(x)) for x in Game.objects.filter(leagues__pk=lo.league)]
            
    c = {'title': title,
         'subtitle': subtitle,
         'options': lo.as_dict,
         'defaults': default.as_dict,
         'leaderboards': json.dumps(leaderboards, cls=DjangoJSONEncoder),
         'leagues': json.dumps(leagues, cls=DjangoJSONEncoder),
         'players': json.dumps(players, cls=DjangoJSONEncoder),
         'games': json.dumps(games, cls=DjangoJSONEncoder),
         'now': timezone.now(),        
         'default_datetime_input_format': datetime_format_python_to_PHP(DATETIME_INPUT_FORMATS[0])         
         }
    
    return render(request, 'CoGs/view_leaderboards.html', context=c)

#===============================================================================
# AJAX providers
#===============================================================================

def ajax_Leaderboards(request, raw=False):
    '''
    A view that returns a JSON string representing requested leaderboards.
    
    This is used with raw=True as well view_Leaderboards to get the leaderboard data.
    
    Should only validly be called from view_Leaderboards when a view is rendered
    or as an AJAX call when requesting a leaderboard refresh because the player name 
    presentation for example has changed. 
    
    Caution: This does not have any way of adjusting the context that the original 
    view received, so any changes to leaderboard content that warrant an update to 
    the view context (for example to display the nature of a filter) should be coming
    through view_Leaderboards (which delivers context to the page). 
    
    The returned leaderboards are in the following rather general structure of
    lists within lists. Some are tuples in the Python which when JSONified for
    the template become lists (arrays) in Javascript. This data structure is central
    to interaction with the front-end template for leaderboard rendering.
    
    Tier1: A list of four value tuples (game.pk, game.BGGid, game.name, Tier2)  
    Tier2: A list of five value tuples (date_time, plays[game], sessions[game], session_detail, Tier3)
    Tier3: A list of six value tuples (player.pk, player.BGGname, player.name, rating.trueskill_eta, rating.plays, rating.victories)
    
    Tier1 is the header for a particular game

    Tier2 is a list of leaderboard snapshots as at the date_time. In the default rendering and standard
    view, this should be a list with one entry, and date_time of the last play as the timestamp. That 
    would indicate a structure that presents the leaderboards for now. These could be filtered of course 
    (be a subset of all leaderboards in the database) by whatever filtering the view otherwise supports.
    The play count and session count for that game up to that time are in this tuple too.   
    
    Tier3 is the leaderboard for that game, a list of players with their trueskill ratings in rank order.
    
    Links to games and players in the leaderboard are built in the template, wrapping a player name in
    a link to nothing or a URL based on player.pk or player.BGGname as per the request.
    '''
    # Start a filter rolling

    lo = get_leaderboard_options(request)

    default = leaderboard_options()
    
    # TODO: Challenge, implement num_games. Need to order games in the query rather than after if possible. Explore.
    #
    # Query thoughts:
    #
    # Session.objects.filter(game=game).count()
    # Performace.objects.filter(session_game=game).count()
    #
    # Game.objects.filter(gfilter).annotate(session_count=Count('sessions').annotate(performance_count=Count('sessions_performances')

    # Process the impact quick view 
    if ("impact" in request.GET):
        sfilter = Q()
        if lo.league != ALL_LEAGUES:
            sfilter &= Q(sessions__league__pk=lo.league)

        if lo.player != ALL_PLAYERS:
            sfilter &= Q(sessions__performances__player__pk=lo.player)
        
        S = Session.objects.filter(sfilter).order_by("-date_time")
        latest_session = S[0] if S.count() > 0 else None

        if not latest_session is None:
            date = latest_session.date_time.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            lo.changed_since = date - timedelta(days=lo.num_days)
            lo.compare_back_to = lo.changed_since
            lo.num_games = 0
   
    (title, subtitle) = get_leaderboard_titles(lo)
      
    # Start the query with an ordered list of all games (lazy, only the SQL created_     
    games = Game.objects.all().annotate(session_count=Count('sessions',distinct=True)).annotate(play_count=Count('sessions__performances',distinct=True)).order_by('-play_count','-session_count')

    # Then build a filter on that sorted list
    gfilter = Q()
    # TODO: Consider supporting multi-selects on all these and using __in set filters
    if lo.league != ALL_LEAGUES:
        gfilter &= Q(sessions__league__pk=lo.league)
    if lo.player != ALL_PLAYERS:
        gfilter &= Q(sessions__performances__player__pk=lo.player)
    if lo.changed_since != NEVER:
        gfilter &= Q(sessions__date_time__gte=lo.changed_since)
    if lo.game != ALL_GAMES: 
        gfilter &= Q(pk=lo.game)
        
    games = games.filter(gfilter).distinct()
    
    if lo.num_games > 0:
        games = games[:lo.num_games]
    
    leaderboards = []
    for game in games:
        if lo.league == ALL_LEAGUES or game.leagues.filter(pk=lo.league).exists():
            # Let's build a list of board times for Tier2 that we want to present, as specified in the request
            # These should reflect the session time stamps at which that leaderboard came to be. We bundle a 
            # session_detail string with each time stamp.
            boards = []
            
            sfilter = Q(game=game)
            if not lo.as_at is None:
                sfilter &= Q(date_time__lte=lo.as_at)
                
            S = Session.objects.filter(sfilter).order_by("-date_time")
            latest_session = S[0] if S.count() > 0 else None
                
            # Fetch the time of the last session in the window changed_since to as_at
            # That will capture the leaderboard as at that time, but of course only if
            # it changed since the requested time, else not. 
            if latest_session:
                last_time = latest_session.date_time
                if (lo.changed_since == default.changed_since or last_time > lo.changed_since) and (lo.as_at == default.as_at or last_time <= lo.as_at):
                    boards.append(latest_session)
            
            # If we have a current leaderboard in the time window changed_since to as_at
            # then we may also want to include its history if requested by:
            #  compare_back_to, compare_til or compare_with
            if len(boards) > 0:
                if (not lo.compare_with is None) or (not lo.compare_back_to is None):
                    sfilter = Q(game=game)
                    
                    if lo.compare_till is None:
                        sfilter &= Q(date_time__lt=latest_session.date_time)
                    else:
                        sfilter &= Q(date_time__lte=lo.compare_till)
    
                    if not lo.compare_back_to is None:
                        # we want to include, if it exists, the last session prior to compare_back_to
                        # As a comparison board for the last snapshot. So it has a reference.
                        ref_session = Session.objects.filter(sfilter & Q(date_time__lt=lo.compare_back_to)).order_by('-date_time')[:1]

                        # Of course if there is no session prior to compare_back_to, we can't do that
                        count = ref_session.count()
                        if count == 0:
                            sfilter &= Q(date_time__gte=lo.compare_back_to)
                        elif count == 1:
                            sfilter &= Q(date_time__gte=ref_session[0].date_time)
                        else:
                            raise ValueError("Internal error: Illegal count of reference sessions.")
                        
                    last_sessions = Session.objects.filter(sfilter).order_by("-date_time")
                    
                    if not lo.compare_with is None and lo.compare_with < last_sessions.count():
                        last_sessions = last_sessions[:lo.compare_with]
    
                    for s in last_sessions:
                        boards.append(s)

            snapshots = []            
            for board in boards:            
                # IF as_at is now, the first time should be the last session time for the game 
                # and thus should translate to the same as what's in the Rating model. 
                # TODO: Perform an integrity check around that and indeed if it's an ordinary
                #       leaderboard presentation check on performance between asat=time (which reads Performance)
                #       and asat=None (which reads Rating).
                # TODO: Consider if performance here improves with a prefetch or such noting that
                #       game.play_counts and game.session_list might run faster with one query rather 
                #       than two.
                time = board.date_time
                detail = board.leaderboard_header(lo.names)            
                lb = game.leaderboard(league=lo.league, asat=time, names=lo.names, indexed=True)
                if not lb is None:
                    counts = game.play_counts(league=lo.league, asat=time)                    
                    snapshot = (localize(time), counts['total'], counts['sessions'], detail, lb)
                    snapshots.append(snapshot)

            if len(snapshots) > 0:                    
                leaderboards.append((game.pk, game.BGGid, game.name, snapshots))

    # raw is asked for on a standard page load, when a true AJAX request is underway it's false.
    return leaderboards if raw else HttpResponse(json.dumps((title, subtitle, lo.as_dict, leaderboards)))

def ajax_Game_Properties(request, pk):
    '''
    A view that returns the basic game properties needed by the Session form to make sensible rendering decisions.
    '''
    game = Game.objects.get(pk=pk)
    
    props = {'individual_play': game.individual_play, 
             'team_play': game.team_play,
             'min_players': game.min_players,
             'max_players': game.max_players,
             'min_players_per_team': game.min_players_per_team,
             'max_players_per_team': game.max_players_per_team
             }
      
    return HttpResponse(json.dumps(props))

def ajax_List(request, model):
    '''
    Support AJAX rendering of lists of objects on the list view. 
    
    To achieve this we instantiate a view_Detail and fetch the object then emit its html view. 
    ''' 
    view = view_List()
    view.request = request
    view.kwargs = {'model':model}
    view.get_queryset()
    
    view_url = reverse("list", kwargs={"model":view.model.__name__})
    json_url = reverse("get_list_html", kwargs={"model":view.model.__name__})
    html = view.as_html()
    
    response = {'view_URL':view_url, 'json_URL':json_url, 'HTML':html}
     
    return HttpResponse(json.dumps(response))

def ajax_Detail(request, model, pk):
    '''
    Support AJAX rendering of objects on the detail view. 
    
    To achieve this we instantiate a view_Detail and fetch the object then emit its html view. 
    ''' 
    view = view_Detail()
    view.request = request
    view.kwargs = {'model':model, 'pk': pk}
    view.get_object()
    
    view_url = reverse("view", kwargs={"model":view.model.__name__,"pk": view.obj.pk})
    json_url = reverse("get_detail_html", kwargs={"model":view.model.__name__,"pk": view.obj.pk})
    html = view.as_html()
    
    response = {'view_URL':view_url, 'json_URL':json_url, 'HTML':html}

    # Add object browser details if available. Should be added by DetailViewExtended    
    if hasattr(view, 'object_browser'):
        response['object_browser'] = view.object_browser
        
        if view.object_browser[0]:
            response['json_URL_prior'] = reverse("get_detail_html", kwargs={"model":view.model.__name__,"pk": view.object_browser[0]})
        else:
            response['json_URL_prior'] = response['json_URL']
             
        if view.object_browser[1]:
            response['json_URL_next'] = reverse("get_detail_html", kwargs={"model":view.model.__name__,"pk": view.object_browser[1]})
        else:
            response['json_URL_next'] = response['json_URL']
     
    return HttpResponse(json.dumps(response))

#===============================================================================
# Special sneaky fixerupper and diagnostic view for testing code snippets
#===============================================================================

def view_About(request):
    '''
    Displays the About page (static HTML wrapped in our base template
    '''
    return

def view_CheckIntegrity(request):
    '''
    Check integrity of database
    
    The check_integrity routines on some models all work with assertions 
    and raise exceptions when integrity errors are found. So this will bail 
    on the first error, and outputs will be on the console not sent to the 
    browser.
    
    All needs some serious tidy up for a productions site.    
    '''
    
    print("Checking all Performances for internal integrity.", flush=True)
    for P in Performance.objects.all():
        print("Performance: {}".format(P), flush=True)
        P.check_integrity()

    print("Checking all Ranks for internal integrity.", flush=True)
    for R in Rank.objects.all():
        print("Rank: {}".format(R), flush=True)
        R.check_integrity()
    
    print("Checking all Sessions for internal integrity.", flush=True)
    for S in Session.objects.all():
        print("Session: {}".format(S), flush=True)
        S.check_integrity()

    print("Checking all Ratings for internal integrity.", flush=True)
    for R in Rating.objects.all():
        print("Rating: {}".format(R), flush=True)
        R.check_integrity()

    return HttpResponse("Passed All Integrity Tests")

def view_RebuildRatings(request):
    html = rebuild_ratings()
    return HttpResponse(html)

def view_UnwindToday(request):
    '''
    A simple view that deletes all sessions (and associated ranks and performances) created today. Used when testing. 
    Dangerous if run on a live database on same day as data was entered clearly. Testing view only.
    '''
    
    unwind_to = date.today() # - timedelta(days=1)
    
    performances = Performance.objects.filter(created_on__gte=unwind_to)
    performances.delete()
    
    ranks = Rank.objects.filter(created_on__gte=unwind_to)
    ranks.delete()

    sessions = Session.objects.filter(created_on__gte=unwind_to)
    sessions.delete()
    
    ratings = Rating.objects.filter(created_on__gte=unwind_to)
    ratings.delete()
    
    # Now for all ratings remaining we have to reset last_play (if test sessions updated that).
    ratings = Rating.objects.filter(Q(last_play__gte=unwind_to)|Q(last_victory__gte=unwind_to))
    for r in ratings:
        r.recalculate_last_play_and_victory()

    html = "Success"
    
    return HttpResponse(html)

def view_Fix(request):

# DONE: Used this to create Performance objects for existing Rank objects
#     sessions = Session.objects.all()
#
#     for session in sessions:
#         ranks = Rank.objects.filter(session=session.id)
#         for rank in ranks:
#             performance = Performance()
#             performance.session = rank.session
#             performance.player = rank.player
#             performance.save()

# Test the rank property of the Performance model
#     table = '<table border=1><tr><th>Performance</th><th>Rank</th></tr>'
#     performances = Performance.objects.all()
#     for performance in performances:
#         table = table + '<tr><td>' + str(performance.id) + '</td><td>' + str(performance.rank) + '</td></tr>'
#     table = table + '</table>'

    #html = force_unique_session_times()
    #html = rebuild_ratings()
    #html = import_sessions()
    
    
    html = "Success"
    
    return HttpResponse(html)

def view_Kill(request, model, pk):
    m = class_from_string('Leaderboards', model)
    o = m.objects.get(pk=pk)
    o.delete()
    
    html = "Success"
    
    return HttpResponse(html)

import csv
from dateutil import parser
from django_generic_view_extensions.html import fmt_str

def import_sessions():
    title = "Import CoGs scoresheet"
    
    result = ""
    sessions = []
    with open('/home/bernd/workspace/CoGs/Seed Data/CoGs Scoresheet - Session Log.csv', newline='') as csvfile:
        reader = csv.DictReader(csvfile, delimiter=',', quotechar='"')
        for row in reader:
            date_time = parser.parse(row["Date"])
            game = row["Game"].strip()
            ranks = {
                row["1st place"].strip(): 1,
                row["2nd place"].strip(): 2,
                row["3rd place"].strip(): 3,
                row["4th place"].strip(): 4,
                row["5th place"].strip(): 5,
                row["6th place"].strip(): 6,
                row["7th place"].strip(): 7
                }
            
            tie_ranks = {}
            for r in ranks:
                if ',' in r:
                    rank = ranks[r]
                    players = r.split(',')
                    for p in players:
                        tie_ranks[p.strip()] = rank
                else:
                    tie_ranks[r] = ranks[r]
                    
            session = (date_time, game, tie_ranks)
            sessions.append(session)

    # Make sure a Game and Player object exists for each game and player
    missing_players = []
    missing_games = []
    for s in sessions:
        g = s[1]
        try:
            Game.objects.get(name=g)
        except Game.DoesNotExist:
            if g and not g in missing_games:
                missing_games.append(g)
        except Game.MultipleObjectsReturned:
            result += "{} exists more than once\n".format(g)
        
        for p in s[2]:
            try:
                Player.objects.get(name_nickname=p)
            except Player.DoesNotExist:
                if p and not p in missing_players:
                    missing_players.append(p)
            except Player.MultipleObjectsReturned:
                result += "{} exists more than once\n".format(p)
            
    if len(missing_games) == 0 and len(missing_players) == 0:
        result += fmt_str(sessions)
        
        Session.objects.all().delete()
        Rank.objects.all().delete()
        Performance.objects.all().delete()
        Rating.objects.all().delete()
        Team.objects.all().delete()
        
        for s in sessions:
            session = Session()
            session.date_time = s[0]
            session.game = Game.objects.get(name=s[1])
            session.league = League.objects.get(name='Hobart')
            session.location = Location.objects.get(name='The Big Blue House')
            session.save()
            
            for p in s[2]:
                if p:
                    rank = Rank()
                    rank.session = session
                    rank.rank = s[2][p]
                    rank.player = Player.objects.get(name_nickname=p)
                    rank.save()
    
                    performance = Performance()
                    performance.session = session
                    performance.player = rank.player
                    performance.save()
                            
            Rating.update(session)
    else:
        result += "Missing Games:\n{}\n".format(fmt_str(missing_games))
        result += "Missing Players:\n{}\n".format(fmt_str(missing_players))
            
    now = datetime.now()
            
    return "<html><body<p>{0}</p><p>It is now {1}.</p><p><pre>{2}</pre></p></body></html>".format(title, now, result)

def rebuild_ratings():
    title = "Rebuild of all ratings"
    pr = cProfile.Profile()
    pr.enable()
    
    Rating.rebuild_all()
    pr.disable()
    
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats('cumulative')
    ps.print_stats()
    result = s.getvalue()
        
    now = datetime.now()

    return "<html><body<p>{0}</p><p>It is now {1}.</p><p><pre>{2}</pre></p></body></html>".format(title, now, result)

def force_unique_session_times():
    '''
    A quick hack to scan through all sessions and ensure none have the same session time
    # TODO: Enforce this when adding or editing sessions because Trueskill is temporal
    # TODO: Technically two game sessions can gave the same time, just not two session involving the same game and a common player. 
    #       This is because the Trueskill for that player at that game needs to have temporally ordered game sessions
    '''
 
    title = "Forced Unique Session Times"
    result = ""
   
    sessions = Session.objects.all().order_by('date_time')
    for s in sessions:
        coincident_sessions = Session.objects.filter(date_time=s.date_time)
        if len(coincident_sessions)>1:
            offset = 1
            for sess in coincident_sessions:
                sess.date_time = sess.date_time + timedelta(seconds=offset)
                sess.save()
                result += "Added {} to {}\n".format(offset, sess)
                offset += 1 

    now = datetime.now()

    return "<html><body<p>{0}</p><p>It is now {1}.</p><p><pre>{2}</pre></p></body></html>".format(title, now, result)      
