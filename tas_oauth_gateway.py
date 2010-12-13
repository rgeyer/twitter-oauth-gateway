import os
import oauth
import logging

from types import *

from google.appengine.ext import webapp
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.ext import db
from google.appengine.api import urlfetch

##################################################################################
# Define models
##################################################################################
class OAuthKey(db.Model):
  api_key = db.StringProperty()
  api_secret = db.StringProperty()

class UserInfo(db.Model):
  name = db.StringProperty()
  username = db.StringProperty()
  picture = db.LinkProperty()
  token = db.StringProperty()
  secret = db.StringProperty()
  service = db.StringProperty(choices=set(["twitter"]))
  id = db.IntegerProperty()

class AuthRequest(db.Model):
  blogUrl = db.LinkProperty()
  authToken = db.ReferenceProperty(oauth.AuthToken)
  oauth_verifier = db.StringProperty()
  user_info = db.ReferenceProperty(UserInfo)

##################################################################################
# Define some helper methods
##################################################################################
def getCallbackUri(auth_request_ds_key):
  isDev = os.environ['SERVER_SOFTWARE'].find('Development') == 0

  if isDev:
    callback_url = 'http://localhost:8989/tas_auth_callback/'
  else:
    callback_url = 'https://oathgw.appspot.com/tas_auth_callback/'

  callback_url += '?key=%s' % auth_request_ds_key
  return callback_url

def getTwitterClient(auth_request_ds_key, callbackUri=None):
  oAuthKey = OAuthKey.all().get()
  if None == callbackUri:
    callbackUri = getCallbackUri(auth_request_ds_key)
  return oauth.TwitterClient(oAuthKey.api_key.encode('ascii'), oAuthKey.api_secret.encode('ascii'), callbackUri)

##################################################################################
# Define classes which are request handlers
##################################################################################
class tas_auth(webapp.RequestHandler):
  def get(self):
    # TODO: Add a call to get user info, and store it as a data store entity
    originating_blog_url = self.request.get('blog_url')

    dsEntity = None
    if 'key' in self.request.arguments():
      logging.debug('We were sent an existing key, sweet! - %s' % self.request.get('key'))
      dsEntity = db.get(self.request.get('key'))
      if None == dsEntity:
        logging.debug('Sadly, there wasn\'t any entity for key %s' % self.request.get('key'))

    if None == dsEntity:
      logging.debug('dsEntity was still None after checking for key')
      dsEntity = AuthRequest()
      query = AuthRequest.all()
      query.filter('blogUrl =', originating_blog_url)
      result = query.get()
      if None == result:
        dsEntity.blogUrl = originating_blog_url
        dsEntity.put()
      else:
        dsEntity = result

    callback_url = getCallbackUri(dsEntity.key())

    if '_wpnonce' in self.request.arguments():
      callback_url += '&_wpnonce=%s' % self.request.get('_wpnonce')

    logging.debug('The callback URI sent to the twitter API will be (%s)' % (callback_url))

    client = getTwitterClient(dsEntity.key(), callback_url)
    redirect_url = client.get_authorization_url()

    authTokenQuery = oauth.AuthToken.all()
    authTokenQuery.filter('token =', redirect_url.split('=').pop(1))
    authTokenEntity = authTokenQuery.fetch(1)

    dsEntity.authToken = authTokenEntity[0]
    db.put(dsEntity)

    self.redirect(redirect_url)

class tas_auth_callback(webapp.RequestHandler):
  def get(self):
    client = getTwitterClient(self.request.get('key'))
    oauth_token = self.request.get('oauth_token')
    oauth_verifier = self.request.get('oauth_verifier')

    # Grab the twitter user info to get the "real" token and secret
    user_info = client.get_user_info(oauth_token, auth_verifier=oauth_verifier)

    query = UserInfo.all()
    query.filter('id =', user_info['id'])
    userResults = query.fetch(1)
    if len(userResults) == 1:
      userEntity = userResults[0]
    else:
      userEntity = UserInfo()

    userEntity.name=user_info['name']
    userEntity.username=user_info['username']
    userEntity.picture=user_info['picture']
    userEntity.token=user_info['token']
    userEntity.secret=user_info['secret']
    userEntity.service=user_info['service']
    userEntity.id=user_info['id']

    userEntity.put()

    logging.debug('Response from get_user_info was %s' % user_info)

    dsEntity = AuthRequest.get(self.request.get('key'))
    dsEntity.user_info = userEntity
    dsEntity.oauth_verifier = oauth_verifier
    db.put(dsEntity)

    redirect_url = '%s/wp-admin/options-general.php?page=83a70cd3-3f32-456d-980d-309169c26ccf&submit_val=OAuthGw&key=%s'% ( dsEntity.blogUrl, dsEntity.key() )

    if '_wpnonce' in self.request.arguments():
      redirect_url += '&_wpnonce=%s' % self.request.get('_wpnonce')

    logging.debug('About to redirect to %s' % redirect_url)

    self.redirect(redirect_url)

class tapi_list_create(webapp.RequestHandler):
  def post(self):
    if 'key' not in self.request.arguments():
      logging.error('Attempting to create list but no key was supplied')
      self.response.set_status(401)
      return

    mode = 'private'
    if 'mode' in self.request.arguments():
      mode = self.request.get('mode')

    client = getTwitterClient(self.request.get('key'))

    dsEntity = AuthRequest.get(self.request.get('key'))

    response = client.make_request(url='https://api.twitter.com/1/%s/lists.json' % dsEntity.user_info.id,
                              token=dsEntity.user_info.token,
                              secret=dsEntity.user_info.secret,
                              additional_params={ "name": self.request.get('name'), "description": self.request.get('description'), 'mode': mode },
                              method=urlfetch.POST,
                              protected=True)
    logging.debug('Response to create list was %s' % response.content)

    self.response.out.write(response.content)

class tapi_list_add(webapp.RequestHandler):
  def post(self):
    if 'key' not in self.request.arguments():
      logging.error('Attempting to add an author to a list but no key was supplied')
      self.response.set_status(401)
      return

    if 'listId' not in self.request.arguments():
      logging.error('Attempting to add an author to a list but no list id was supplied')
      self.response.set_status(400)
      return

    if 'authorId' not in self.request.arguments():
      logging.error('Attempting to add an author to a list but no author id was supplied')
      self.response.set_status(400)
      return

    client = getTwitterClient(self.request.get('key'))

    dsEntity = AuthRequest.get(self.request.get('key'))

    response = client.make_request(url='https://api.twitter.com/1/%s/%s/members.json' % (dsEntity.user_info.id, self.request.get('listId')),
                                   token=dsEntity.user_info.token,
                                   secret=dsEntity.user_info.secret,
                                   additional_params={'id': self.request.get('authorId')},
                                   method=urlfetch.POST,
                                   protected=True
                                   )

    self.response.out.write(response.content)

class tapi_get_status(webapp.RequestHandler):
  def post(self):
    if 'key' not in self.request.arguments():
      logging.error('Attempting to fetch a status but no key was supplied')
      self.response.set_status(401)
      return

    if 'id' not in self.request.arguments():
      logging.error('Attempting to fetch a status but no status id was supplied')
      self.response.set_status(400)
      return

    client = getTwitterClient(self.request.get('key'))

    dsEntity = AuthRequest.get(self.request.get('key'))

    response = client.make_request(url='https://api.twitter.com/1/statuses/show/%s.json' % self.request.get('id'),
                                   token=dsEntity.user_info.token,
                                   secret=dsEntity.user_info.secret,
                                   method=urlfetch.GET,
                                   protected=True
                                   )


    self.response.out.write(response.content)


class auth_token_job(webapp.RequestHandler):
  def get(self):
    logging.info("Running AuthToken clearing CRON job")
    listOfAssociatedAuthTokens = []
    authRequestQuery = AuthRequest.all()
    for authReq in authRequestQuery:
      listOfAssociatedAuthTokens.append(authReq.authToken.key())

    authTokenQuery = oauth.AuthToken.all()
    for authToken in authTokenQuery:
      if authToken.key() not in listOfAssociatedAuthTokens:
        logging.debug('Deleting AuthToken %s<br/>' % authToken.key())
        authToken.delete()


application = webapp.WSGIApplication( [
  ('/tas_auth/', tas_auth),
  ('/tas_auth_callback/', tas_auth_callback),
  ('/jobs/authTokens/clear', auth_token_job),
  ('/tapi/list/create/', tapi_list_create),
  ('/tapi/list/add/', tapi_list_add),
  ('/tapi/statuses/show/', tapi_get_status)
  ], debug=True)

def main():
  logging.getLogger().setLevel(logging.DEBUG)

  # Bootstrap OAuthKey for new installations and development
  if None == OAuthKey.all(keys_only=True).get():
    logging.warn("Application started with bogus values for the OAuth API, you need to provide real values in the Data Viewer for things to work.")
    OAuthKey(api_key='foo', api_secret='bar').put()

  run_wsgi_app(application)

if __name__ == "__main__":
  main()